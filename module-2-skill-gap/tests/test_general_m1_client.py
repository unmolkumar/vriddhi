"""General engine (v2) - module 1 client: REST errors, per-version cache, low-confidence resolution, fixture twin."""
import httpx
import pytest

from src.general.m1_client import (
    LOW_CONFIDENCE, FixtureM1Client, M1Client, M1Error, OccupationMatch, resolution,
)

RN = "29-1141.00"


def _client(handler, tmp_path, calls=None):
    def counting(request):
        if calls is not None:
            calls.append(request.url.path)
        return handler(request)
    return M1Client("http://m1.test", client=httpx.Client(transport=httpx.MockTransport(counting)), cache_dir=tmp_path)


def _m1(version="2.0.0"):
    def handler(request):
        path = request.url.path
        if path == "/openapi.json":
            return httpx.Response(200, json={"info": {"version": version}})
        if path.endswith("/requirements"):
            return httpx.Response(200, json=[{"soc_code": RN, "item_type": "skill", "item_name": "Active Listening"}])
        if path == "/api/v1/occupations/search":
            return httpx.Response(200, json=[
                {"soc_code": RN, "title": "Registered Nurses", "confidence": 1.0, "method": "india_alias_exact",
                 "matched_term": request.url.params["q"]}])
        return httpx.Response(404, json={"detail": "not found"})
    return handler


def test_requirements_are_cached_per_module_1_version(tmp_path):
    calls = []
    client = _client(_m1(), tmp_path, calls)
    assert client.requirements(RN)[0]["item_name"] == "Active Listening"
    assert client.requirements(RN)[0]["item_name"] == "Active Listening"
    assert calls.count(f"/api/v1/occupations/{RN}/requirements") == 1          # second read from disk
    assert (tmp_path / "2.0.0" / f"{RN}.requirements.json").exists()
    newer = _client(_m1("2.1.0"), tmp_path, calls)                              # module 1 rebuilt: fetched again
    newer.requirements(RN)
    assert calls.count(f"/api/v1/occupations/{RN}/requirements") == 2


def test_unknown_version_is_never_cached(tmp_path):
    def no_openapi(request):
        return httpx.Response(404) if request.url.path == "/openapi.json" else _m1()(request)
    calls = []
    client = _client(no_openapi, tmp_path, calls)
    client.requirements(RN), client.requirements(RN)
    assert client.version() == "unknown" and calls.count(f"/api/v1/occupations/{RN}/requirements") == 2


@pytest.mark.parametrize("exc, code", [(httpx.ConnectError("refused"), "unreachable"),
                                       (httpx.ReadTimeout("slow"), "timeout")])
def test_transport_failures_become_structured_errors(tmp_path, exc, code):
    def handler(request):
        raise exc
    with pytest.raises(M1Error) as e:
        _client(handler, tmp_path).search("nurse")
    assert e.value.code == code


def test_http_errors_become_structured_errors(tmp_path):
    client = _client(_m1(), tmp_path)
    with pytest.raises(M1Error) as e:
        client.profile("99-9999.00")
    assert (e.value.code, e.value.status) == ("not_found", 404)
    broken = _client(lambda r: httpx.Response(500, json={"detail": "boom"}), tmp_path)
    with pytest.raises(M1Error) as e:
        broken.search("nurse")
    assert e.value.code == "bad_response"


def test_search_resolves_with_confidence(tmp_path):
    res = _client(_m1(), tmp_path).search("staff nurse")
    assert res.matches[0].soc_code == RN and not res.low_confidence and res.did_you_mean == []


def test_low_confidence_and_ties_ask_did_you_mean():
    weak = resolution("cook", [OccupationMatch(soc_code="35-1011.00", title="Chefs and Head Cooks",
                                               confidence=LOW_CONFIDENCE - 0.1)])
    assert weak.low_confidence and weak.did_you_mean == ["Chefs and Head Cooks"]
    tie = resolution("engineer", [OccupationMatch(soc_code="17-2051.00", title="Civil Engineers", confidence=0.9),
                                  OccupationMatch(soc_code="17-2141.00", title="Mechanical Engineers", confidence=0.88)])
    assert tie.low_confidence and tie.did_you_mean == ["Civil Engineers", "Mechanical Engineers"]
    same_family = resolution("nurse", [OccupationMatch(soc_code=RN, title="Registered Nurses", confidence=1.0),
                                       OccupationMatch(soc_code="29-1141.01", title="Acute Care Nurses", confidence=0.98)])
    assert not same_family.low_confidence
    assert resolution("xyz", []).low_confidence


def test_fixture_client_mirrors_the_interface():
    f = FixtureM1Client()
    assert f.version() == "2.0.0" and len(f.occupations) == 15
    assert f.search("registered nurse").matches[0].soc_code == RN
    assert len(f.requirements(RN)) == 280 and f.profile(RN)["title"] == "Registered Nurses"
    with pytest.raises(M1Error):
        f.requirements("00-0000.00")
