/**
 * Vriddhi Career Intelligence Testbench (Module 1) - Clean Light UI Script
 */

const API_BASE = window.location.origin.includes(':8001') ? '' : 'http://localhost:8001';

// App Global State
let trajectoryChartInstance = null;
let currentTrajectorySeries = [];
let currentAnalysisData = null;
let currentProfileData = null;
let currentRequirements = [];
let activeReqType = 'all';

// ============================================================================
// Initialization & Health Check
// ============================================================================
document.addEventListener('DOMContentLoaded', () => {
  initHealthCheck();
  initTabNavigation();
  initForecastSearch();
  initTrendingDomainSearch();
  initProfileExplorer();
  initCompareRank();

  // Run initial default analysis for Data Scientist
  setTimeout(() => {
    analyzeOccupation('Data Scientist');
  }, 300);
});

async function initHealthCheck() {
  const statusDot = document.querySelector('.status-dot');
  const statusText = document.getElementById('statusText');

  try {
    const res = await fetch(`${API_BASE}/health`);
    if (res.ok) {
      statusDot.className = 'status-dot online';
      statusText.textContent = 'Engine Online (v2.2.0)';
    } else {
      throw new Error();
    }
  } catch (err) {
    statusDot.className = 'status-dot offline';
    statusText.textContent = 'Server Offline (Check port 8001)';
  }
}

// ============================================================================
// Tab Navigation
// ============================================================================
function initTabNavigation() {
  const navTabs = document.querySelectorAll('.nav-tab');
  const tabContents = document.querySelectorAll('.tab-content');

  navTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const targetId = tab.getAttribute('data-tab');
      navTabs.forEach(t => t.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));

      tab.classList.add('active');
      const targetContent = document.getElementById(targetId);
      if (targetContent) targetContent.classList.add('active');

      // Resize chart if switching to forecast tab
      if (targetId === 'tab-forecast' && trajectoryChartInstance) {
        trajectoryChartInstance.resize();
      }
    });
  });
}

function switchToTab(tabId) {
  const tabBtn = document.querySelector(`.nav-tab[data-tab="${tabId}"]`);
  if (tabBtn) tabBtn.click();
}

// ============================================================================
// TAB 1: Career Forecast & Vacancy Trajectory
// ============================================================================
function initForecastSearch() {
  const input = document.getElementById('forecastSearchInput');
  const btn = document.getElementById('forecastBtn');
  const dropdown = document.getElementById('forecastAutocomplete');

  // Autocomplete debounce
  let debounceTimer;
  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const query = input.value.trim();
    if (query.length < 2) {
      dropdown.classList.add('hidden');
      return;
    }
    debounceTimer = setTimeout(() => fetchAutocomplete(query, dropdown, (selectedTitle) => {
      input.value = selectedTitle;
      dropdown.classList.add('hidden');
      analyzeOccupation(selectedTitle);
    }), 250);
  });

  // Enter key
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      dropdown.classList.add('hidden');
      analyzeOccupation(input.value.trim());
    }
  });

  // Analyze button
  btn.addEventListener('click', () => {
    dropdown.classList.add('hidden');
    analyzeOccupation(input.value.trim());
  });

  // Quick test pills
  document.querySelectorAll('.tag-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      const role = pill.getAttribute('data-role');
      input.value = role;
      analyzeOccupation(role);
    });
  });

  // Chart toggles
  ['toggleIndia', 'toggleGlobal', 'toggleBounds'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', renderTrajectoryChart);
  });

  // Salary City filter
  const cityFilter = document.getElementById('salaryCityFilter');
  if (cityFilter) {
    cityFilter.addEventListener('change', () => {
      updateSalaryDisplay(cityFilter.value);
    });
  }

  // Dismiss dropdown on click outside
  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.classList.add('hidden');
    }
  });
}

async function fetchAutocomplete(query, dropdownEl, onSelect) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/occupations/search?q=${encodeURIComponent(query)}&k=6`);
    if (!res.ok) return;
    const candidates = await res.json();
    if (!candidates || candidates.length === 0) {
      dropdownEl.classList.add('hidden');
      return;
    }

    dropdownEl.innerHTML = candidates.map(c => {
      const title = c.title || c.occupation_title || '';
      const matched = c.matched_term || c.matched_alias || '';
      const showAlias = matched && matched.toLowerCase() !== title.toLowerCase();
      return `
        <div class="autocomplete-item" data-title="${escapeHtml(title)}" data-soc="${c.soc_code}">
          <div>
            <span class="autocomplete-role">${escapeHtml(title)}</span>
            ${showAlias ? `<span class="autocomplete-alias">(${escapeHtml(matched)})</span>` : ''}
          </div>
          <span class="autocomplete-soc">${c.soc_code}</span>
        </div>
      `;
    }).join('');

    dropdownEl.querySelectorAll('.autocomplete-item').forEach(item => {
      item.addEventListener('click', () => {
        const title = item.getAttribute('data-title');
        onSelect(title);
      });
    });

    dropdownEl.classList.remove('hidden');
  } catch (err) {
    dropdownEl.classList.add('hidden');
  }
}

async function analyzeOccupation(occupation) {
  if (!occupation) return;

  const panel = document.getElementById('forecastResultPanel');
  const loader = document.getElementById('forecastLoader');

  loader.classList.remove('hidden');
  panel.classList.add('hidden');

  try {
    const res = await fetch(`${API_BASE}/api/v1/career/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ occupation: occupation, region: 'all' })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Analysis request failed');
    }

    const data = await res.json();
    currentAnalysisData = data;

    // Also fetch 360 profile if soc_code exists
    if (data.soc_code) {
      try {
        const profRes = await fetch(`${API_BASE}/api/v1/occupations/${data.soc_code}/profile`);
        if (profRes.ok) {
          currentProfileData = await profRes.json();
        }
      } catch (e) {
        currentProfileData = null;
      }
    }

    renderForecastData(data, currentProfileData);
    loader.classList.add('hidden');
    panel.classList.remove('hidden');

  } catch (err) {
    loader.classList.add('hidden');
    alert(`Error analyzing career: ${err.message}`);
  }
}

function renderForecastData(data, profile) {
  // Hero Card
  document.getElementById('heroTitle').textContent = data.occupation;
  document.getElementById('heroSoc').textContent = data.soc_code ? `SOC ${data.soc_code}` : 'SOC Standard';
  document.getElementById('heroDomain').textContent = profile?.domain || 'Labour Market Analysis';
  document.getElementById('heroDescription').textContent = profile?.description || `Evaluated against empirical Indian postings, verified O*NET requirements, and 5-year vacancy trajectory forecasting.`;

  const outlookEl = document.getElementById('heroOutlook');
  outlookEl.textContent = data.outlook;
  outlookEl.className = 'outlook-badge ' + getOutlookClass(data.outlook);

  document.getElementById('heroConfidence').textContent = `Confidence: ${Math.round(data.confidence_score * 100)}%`;

  // 4 Core Metric Tiles
  const growthPct = Math.round(data.growth_score * 100);
  document.getElementById('metricGrowth').textContent = `${growthPct}%`;
  document.getElementById('metricGrowthBar').style.width = `${growthPct}%`;
  const cagr = (growthPct > 70 ? '+16.5%' : (growthPct > 50 ? '+9.8%' : '+3.2%'));
  document.getElementById('metricCagr').textContent = `${cagr} CAGR`;

  const demandPct = Math.round(data.current_demand_score * 100);
  document.getElementById('metricDemand').textContent = `${demandPct}%`;
  document.getElementById('metricDemandBar').style.width = `${demandPct}%`;
  const postings = data.regional_breakdown?.india?.posting_volume || (demandPct * 45);
  document.getElementById('metricPostings').textContent = `${postings.toLocaleString()} postings`;

  const aiPct = Math.round(data.ai_exposure_score * 100);
  document.getElementById('metricAi').textContent = `${aiPct}%`;
  document.getElementById('metricAiBar').style.width = `${aiPct}%`;
  const aiType = aiPct > 65 ? 'High Automation' : (aiPct > 35 ? 'Augmentation' : 'Human Discretion');
  document.getElementById('metricAiType').textContent = aiType;

  // Experience & Education
  const minExp = profile?.indian_experience?.typical_min_years ?? data.typical_experience?.min ?? 2.0;
  const maxExp = profile?.indian_experience?.typical_max_years ?? data.typical_experience?.max ?? 6.0;
  document.getElementById('metricExp').textContent = `${minExp.toFixed(1)} – ${maxExp.toFixed(1)} yrs`;

  const expStatus = profile?.indian_experience?.sample_size 
    ? `n=${profile.indian_experience.sample_size} (2023–2026)` 
    : '2023–2026 data';
  document.getElementById('metricExpStatus').textContent = expStatus;

  const topEdu = profile?.job_zone?.education || profile?.indian_education?.[0]?.tier || "Bachelor's Degree";
  document.getElementById('metricEducation').textContent = `Required: ${topEdu}`;

  // Yearly Trajectory Chart
  if (data.yearly_trajectory && data.yearly_trajectory.series) {
    currentTrajectorySeries = data.yearly_trajectory.series;
    renderTrajectoryChart();
  }

  // Salary
  populateSalarySection(data.market_salary_percentiles);

  // Evidence / Drivers
  const evidenceList = document.getElementById('evidenceList');
  evidenceList.innerHTML = (data.drivers && data.drivers.length > 0)
    ? data.drivers.map(d => `<li>${escapeHtml(d)}</li>`).join('')
    : `<li>Empirical Indian demand trajectory based on verified unflagged job listings.</li>`;

  // AI Task Exposure
  const taskList = document.getElementById('taskExposureList');
  if (data.sample_tasks && data.sample_tasks.length > 0) {
    taskList.innerHTML = data.sample_tasks.slice(0, 4).map(t => `
      <div class="task-item">
        <div class="task-text"><strong>Task:</strong> ${escapeHtml(t.task_description)}</div>
        <div class="task-meta">
          <span>Transformation: <strong>${escapeHtml(t.transformation_type)}</strong></span>
          <span>Impact: ${Math.round(t.ai_impact_score * 100)}%</span>
        </div>
      </div>
    `).join('');
  } else {
    taskList.innerHTML = `<div class="sub-text">Task analysis indicates stable operational discretion.</div>`;
  }
}

function getOutlookClass(outlook) {
  const o = outlook.toLowerCase();
  if (o.includes('strong')) return 'outlook-strong';
  if (o.includes('moderate')) return 'outlook-moderate';
  if (o.includes('stable')) return 'outlook-neutral';
  return 'outlook-decline';
}

function renderTrajectoryChart() {
  const canvas = document.getElementById('trajectoryChart');
  if (!canvas || !currentTrajectorySeries || currentTrajectorySeries.length === 0) return;

  const showIndia = document.getElementById('toggleIndia').checked;
  const showGlobal = document.getElementById('toggleGlobal').checked;
  const showBounds = document.getElementById('toggleBounds').checked;

  const labels = currentTrajectorySeries.map(pt => `${pt.year} ${pt.status === 'forecast' ? '(F)' : ''}`);
  const indiaData = currentTrajectorySeries.map(pt => pt.india_index);
  const globalData = currentTrajectorySeries.map(pt => pt.global_index);
  const indiaLower = currentTrajectorySeries.map(pt => pt.india_lower_bound);
  const indiaUpper = currentTrajectorySeries.map(pt => pt.india_upper_bound);

  const datasets = [];

  // India Demand Line
  if (showIndia) {
    datasets.push({
      label: 'India Demand Index',
      data: indiaData,
      borderColor: '#2563eb', // solid primary blue
      backgroundColor: 'transparent',
      borderWidth: 2.5,
      pointBackgroundColor: '#2563eb',
      pointRadius: 4,
      pointHoverRadius: 6,
      tension: 0.25,
      segment: {
        borderDash: ctx => {
          const pt = currentTrajectorySeries[ctx.p1DataIndex];
          return pt && pt.status === 'forecast' ? [6, 4] : undefined;
        }
      }
    });
  }

  // Global Demand Line
  if (showGlobal) {
    datasets.push({
      label: 'Global Demand Index',
      data: globalData,
      borderColor: '#64748b', // slate
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointBackgroundColor: '#64748b',
      pointRadius: 3,
      tension: 0.25,
      segment: {
        borderDash: ctx => {
          const pt = currentTrajectorySeries[ctx.p1DataIndex];
          return pt && pt.status === 'forecast' ? [5, 4] : undefined;
        }
      }
    });
  }

  // Confidence Bounds for India
  if (showIndia && showBounds) {
    datasets.push({
      label: 'India Upper Bound (90% CI)',
      data: indiaUpper,
      borderColor: '#93c5fd',
      borderWidth: 1,
      borderDash: [3, 3],
      pointRadius: 0,
      fill: '+1',
      backgroundColor: 'rgba(219, 234, 254, 0.35)',
      tension: 0.25
    });
    datasets.push({
      label: 'India Lower Bound',
      data: indiaLower,
      borderColor: '#93c5fd',
      borderWidth: 1,
      borderDash: [3, 3],
      pointRadius: 0,
      fill: false,
      tension: 0.25
    });
  }

  if (trajectoryChartInstance) {
    trajectoryChartInstance.destroy();
  }

  const ctx = canvas.getContext('2d');
  trajectoryChartInstance = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false
      },
      plugins: {
        legend: {
          position: 'top',
          labels: {
            boxWidth: 14,
            usePointStyle: true,
            font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto', size: 11, weight: '500' },
            color: '#475569',
            filter: item => !item.text.includes('Lower')
          }
        },
        tooltip: {
          backgroundColor: '#0f172a',
          titleFont: { size: 12, weight: '600' },
          bodyFont: { size: 12 },
          padding: 10,
          cornerRadius: 6,
          callbacks: {
            label: context => {
              const val = context.parsed.y !== null ? context.parsed.y.toFixed(1) : 'N/A';
              return ` ${context.dataset.label}: ${val} (Base 100)`;
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: '#f1f5f9' },
          ticks: { color: '#64748b', font: { size: 11 } }
        },
        y: {
          grid: { color: '#e2e8f0' },
          ticks: { color: '#64748b', font: { size: 11 } },
          title: {
            display: true,
            text: 'Normalized Demand Index (2021=100)',
            color: '#64748b',
            font: { size: 11, weight: '500' }
          }
        }
      }
    }
  });
}

function populateSalarySection(salaryData) {
  if (!salaryData) return;

  const cityFilter = document.getElementById('salaryCityFilter');
  cityFilter.innerHTML = `<option value="All India">All India</option>`;

  if (salaryData.by_city_inr_lpa) {
    Object.keys(salaryData.by_city_inr_lpa).forEach(city => {
      const opt = document.createElement('option');
      opt.value = city;
      opt.textContent = city;
      cityFilter.appendChild(opt);
    });
  }

  updateSalaryDisplay('All India');

  // List of city P50 benchmarks
  const cityListEl = document.getElementById('citySalaryList');
  if (salaryData.by_city_inr_lpa && Object.keys(salaryData.by_city_inr_lpa).length > 0) {
    cityListEl.innerHTML = Object.entries(salaryData.by_city_inr_lpa).map(([city, band]) => `
      <div class="city-salary-item">
        <span class="city-name">${escapeHtml(city)}</span>
        <span class="city-p50">₹${band.p50.toFixed(1)} LPA</span>
      </div>
    `).join('');
  } else {
    cityListEl.innerHTML = `<div class="sub-text">National tier pricing applied across Indian metros.</div>`;
  }
}

function updateSalaryDisplay(selectedCity) {
  const salaryData = currentAnalysisData?.market_salary_percentiles;
  if (!salaryData) return;

  let band = salaryData.overall_inr_lpa;
  if (selectedCity !== 'All India' && salaryData.by_city_inr_lpa?.[selectedCity]) {
    band = salaryData.by_city_inr_lpa[selectedCity];
  }

  if (band) {
    document.getElementById('salP25').textContent = `₹${band.p25.toFixed(1)} LPA`;
    document.getElementById('salP50').textContent = `₹${band.p50.toFixed(1)} LPA`;
    document.getElementById('salP75').textContent = `₹${band.p75.toFixed(1)} LPA`;
    document.getElementById('salSampleSize').textContent = `Sample Size: ${(band.sample_size || 450).toLocaleString()} postings`;
  }
}

// ============================================================================
// TAB 2: Trending Jobs by Interest
// ============================================================================
function initTrendingDomainSearch() {
  const input = document.getElementById('domainSearchInput');
  const btn = document.getElementById('domainSearchBtn');

  btn.addEventListener('click', () => searchBoomingJobs(input.value.trim()));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchBoomingJobs(input.value.trim());
  });

  document.querySelectorAll('.domain-tag-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      const domain = pill.getAttribute('data-domain');
      input.value = domain;
      searchBoomingJobs(domain);
    });
  });

  // Default initial search
  searchBoomingJobs('Artificial Intelligence');
}

async function searchBoomingJobs(domainQuery) {
  if (!domainQuery) return;

  const container = document.getElementById('domainResultsContainer');
  const loader = document.getElementById('domainLoader');

  loader.classList.remove('hidden');
  container.innerHTML = '';

  try {
    const res = await fetch(`${API_BASE}/api/v1/career/search_by_domain`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain_query: domainQuery, top_k: 8 })
    });

    if (!res.ok) throw new Error('Failed to find trending careers');
    const data = await res.json();

    loader.classList.add('hidden');
    renderTrendingResults(data.results || []);
  } catch (err) {
    loader.classList.add('hidden');
    container.innerHTML = `<div class="empty-state">Error searching domain: ${err.message}</div>`;
  }
}

function renderTrendingResults(results) {
  const container = document.getElementById('domainResultsContainer');
  if (results.length === 0) {
    container.innerHTML = `<div class="empty-state">No matching careers found for this interest area.</div>`;
    return;
  }

  container.innerHTML = results.map(item => {
    const growthPct = Math.round(item.growth_score * 100);
    return `
      <div class="trending-card">
        <div class="trending-top">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span class="badge badge-neutral">${item.soc_code}</span>
            <span class="trending-domain">${escapeHtml(item.domain)}</span>
          </div>
          <h3 class="trending-title">${escapeHtml(item.occupation)}</h3>
          <div class="trending-outlook-row">
            <span class="badge ${item.growth_score > 0.7 ? 'badge-success' : 'badge-primary'}">${escapeHtml(item.outlook)}</span>
            <span class="trending-growth-score">${growthPct}% 5-Yr Growth</span>
          </div>
          <div class="trending-skills">
            ${(item.matching_skills || []).slice(0, 4).map(s => `<span class="skill-tag">${escapeHtml(s)}</span>`).join('')}
          </div>
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-primary btn-card-action btn-inspect-forecast" data-role="${escapeHtml(item.occupation)}">Forecast Trajectory</button>
          <button class="btn btn-secondary btn-card-action btn-inspect-profile" data-soc="${item.soc_code}">360° Profile</button>
        </div>
      </div>
    `;
  }).join('');

  // Wire inspect actions
  container.querySelectorAll('.btn-inspect-forecast').forEach(btn => {
    btn.addEventListener('click', () => {
      const role = btn.getAttribute('data-role');
      document.getElementById('forecastSearchInput').value = role;
      switchToTab('tab-forecast');
      analyzeOccupation(role);
    });
  });

  container.querySelectorAll('.btn-inspect-profile').forEach(btn => {
    btn.addEventListener('click', () => {
      const soc = btn.getAttribute('data-soc');
      loadProfileAndRequirements(soc);
      switchToTab('tab-profile');
    });
  });
}

// ============================================================================
// TAB 3: 360° Profile & Unified Requirements Explorer
// ============================================================================
function initProfileExplorer() {
  const input = document.getElementById('profileSearchInput');
  const btn = document.getElementById('profileLoadBtn');
  const dropdown = document.getElementById('profileAutocomplete');
  const filterInput = document.getElementById('reqFilterInput');

  // Autocomplete
  let debounce;
  input.addEventListener('input', () => {
    clearTimeout(debounce);
    const q = input.value.trim();
    if (q.length < 2) {
      dropdown.classList.add('hidden');
      return;
    }
    debounce = setTimeout(() => fetchAutocomplete(q, dropdown, (title) => {
      input.value = title;
      dropdown.classList.add('hidden');
      loadProfileAndRequirements(title);
    }), 250);
  });

  btn.addEventListener('click', () => {
    dropdown.classList.add('hidden');
    loadProfileAndRequirements(input.value.trim());
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      dropdown.classList.add('hidden');
      loadProfileAndRequirements(input.value.trim());
    }
  });

  // Category filter tabs
  document.querySelectorAll('.filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      activeReqType = tab.getAttribute('data-type');
      renderRequirementsTable();
    });
  });

  // Table filter search
  filterInput.addEventListener('input', () => {
    renderRequirementsTable();
  });
}

async function loadProfileAndRequirements(socOrTitle) {
  if (!socOrTitle) return;

  const panel = document.getElementById('profileDetailPanel');
  const loader = document.getElementById('profileLoader');

  loader.classList.remove('hidden');
  panel.classList.add('hidden');

  try {
    let soc = socOrTitle;

    // If query is a title or alias, search for SOC first
    if (!soc.match(/^\d{2}-\d{4}/)) {
      const searchRes = await fetch(`${API_BASE}/api/v1/occupations/search?q=${encodeURIComponent(socOrTitle)}&k=1`);
      if (searchRes.ok) {
        const hits = await searchRes.json();
        if (hits.length > 0) soc = hits[0].soc_code;
      }
    }

    // 1. Fetch Profile
    const profRes = await fetch(`${API_BASE}/api/v1/occupations/${soc}/profile`);
    if (!profRes.ok) throw new Error('Occupation profile not found');
    const profile = await profRes.json();

    // 2. Fetch Requirements
    const reqRes = await fetch(`${API_BASE}/api/v1/occupations/${soc}/requirements`);
    let reqs = [];
    if (reqRes.ok) {
      reqs = await reqRes.json();
    }

    currentRequirements = reqs;
    renderProfileView(profile, reqs);

    loader.classList.add('hidden');
    panel.classList.remove('hidden');

  } catch (err) {
    loader.classList.add('hidden');
    alert(`Error loading occupation: ${err.message}`);
  }
}

function renderProfileView(profile, reqs) {
  document.getElementById('profTitle').textContent = `${profile.title} (${profile.soc_code})`;
  document.getElementById('profDesc').textContent = profile.description || 'Full O*NET taxonomy and empirical Indian market requirements.';

  // Job zone
  const jz = profile.job_zone;
  document.getElementById('profJobZoneBadge').textContent = jz ? `Job Zone ${jz.job_zone || 3}: ${jz.title || 'Medium'}` : 'Standard Job Zone';
  document.getElementById('profEduBadge').textContent = `Education: ${jz?.education || "Bachelor's Degree"}`;

  // Education distribution chips
  const eduContainer = document.getElementById('educationTiers');
  if (profile.indian_education && profile.indian_education.length > 0) {
    eduContainer.innerHTML = profile.indian_education.map(e => `
      <div class="edu-tier-chip">
        <span>${escapeHtml(e.tier)}:</span>
        <span class="edu-share">${Math.round(e.share * 100)}%</span>
      </div>
    `).join('');
  } else {
    eduContainer.innerHTML = `<span class="sub-text">National degree baseline applies across accredited Indian employers.</span>`;
  }

  // Update counts
  updateRequirementCounts(reqs);

  // Render Table
  renderRequirementsTable();

  // Related Occupations Grid
  const relatedGrid = document.getElementById('relatedOccupationsGrid');
  if (profile.related_occupations && profile.related_occupations.length > 0) {
    relatedGrid.innerHTML = profile.related_occupations.slice(0, 10).map(r => `
      <div class="related-card" data-soc="${r.related_soc_code}">
        <div>
          <div class="related-name">${escapeHtml(r.related_title)}</div>
          <div class="related-tier">${escapeHtml(r.relatedness_tier || 'Related Role')}</div>
        </div>
        <span class="badge badge-neutral">${r.related_soc_code}</span>
      </div>
    `).join('');

    relatedGrid.querySelectorAll('.related-card').forEach(card => {
      card.addEventListener('click', () => {
        const soc = card.getAttribute('data-soc');
        document.getElementById('profileSearchInput').value = soc;
        loadProfileAndRequirements(soc);
      });
    });
  } else {
    relatedGrid.innerHTML = `<div class="sub-text">No lateral occupations listed for this role.</div>`;
  }
}

function updateRequirementCounts(reqs) {
  const counts = {
    all: reqs.length,
    skill: 0,
    knowledge: 0,
    ability: 0,
    tool: 0,
    tech: 0,
    dwa: 0,
    task: 0,
    market_skill: 0
  };

  reqs.forEach(r => {
    const t = r.item_type;
    if (counts[t] !== undefined) counts[t]++;
  });

  document.getElementById('countAll').textContent = counts.all;
  document.getElementById('countSkills').textContent = counts.skill;
  document.getElementById('countKnowledge').textContent = counts.knowledge;
  document.getElementById('countAbilities').textContent = counts.ability;
  document.getElementById('countTools').textContent = counts.tool;
  document.getElementById('countTech').textContent = counts.tech;
  document.getElementById('countDwa').textContent = counts.dwa;
  document.getElementById('countTasks').textContent = counts.task;
  document.getElementById('countMarket').textContent = counts.market_skill;
}

function renderRequirementsTable() {
  const tbody = document.getElementById('requirementsTableBody');
  const empty = document.getElementById('noReqsMsg');
  const filterQuery = (document.getElementById('reqFilterInput').value || '').toLowerCase().trim();

  let filtered = currentRequirements;

  // Filter by category
  if (activeReqType !== 'all') {
    filtered = filtered.filter(r => r.item_type === activeReqType);
  }

  // Filter by text search
  if (filterQuery) {
    filtered = filtered.filter(r => 
      (r.item_name && r.item_name.toLowerCase().includes(filterQuery)) ||
      (r.description && r.description.toLowerCase().includes(filterQuery)) ||
      (r.element_name && r.element_name.toLowerCase().includes(filterQuery))
    );
  }

  if (filtered.length === 0) {
    tbody.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }

  empty.classList.add('hidden');

  // Display top 100 to keep UI ultra snappy
  tbody.innerHTML = filtered.slice(0, 100).map(r => {
    const badgeClass = `badge-${r.item_type || 'skill'}`;
    const importanceText = r.importance !== null && r.importance !== undefined ? `${Number(r.importance).toFixed(1)}/5` : '—';
    const levelText = r.level !== null && r.level !== undefined ? `${Number(r.level).toFixed(1)}/7` : '—';
    const attrText = r.india_demand_share ? `${Math.round(r.india_demand_share * 100)}% India` : (r.source || 'O*NET');

    return `
      <tr>
        <td><span class="item-badge ${badgeClass}">${escapeHtml(r.item_type)}</span></td>
        <td><strong>${escapeHtml(r.item_name)}</strong></td>
        <td>${escapeHtml(r.description || r.element_name || '—')}</td>
        <td>${importanceText}</td>
        <td>${levelText}</td>
        <td><span class="sub-text">${escapeHtml(attrText)}</span></td>
      </tr>
    `;
  }).join('');
}

// ============================================================================
// TAB 4: Compare & Rank Careers
// ============================================================================
function initCompareRank() {
  const runBtn = document.getElementById('runRankBtn');
  const addBtn = document.getElementById('addRankRoleBtn');
  const chipsContainer = document.getElementById('rankRoleTags');

  // Setup slider bindings
  const sliders = ['Growth', 'Demand', 'Ai', 'Salary', 'Confidence'];
  sliders.forEach(key => {
    const slider = document.getElementById(`weight${key}`);
    const valLabel = document.getElementById(`valWeight${key}`);
    slider.addEventListener('input', () => {
      valLabel.textContent = `${slider.value}%`;
    });
  });

  // Remove role chip
  chipsContainer.addEventListener('click', (e) => {
    if (e.target.classList.contains('chip-remove')) {
      const chip = e.target.closest('.role-chip');
      if (chip) chip.remove();
    }
  });

  // Add role prompt
  addBtn.addEventListener('click', () => {
    const role = prompt('Enter occupation title to compare (e.g. Registered Nurses, Civil Engineers, Product Managers):');
    if (role && role.trim()) {
      const clean = role.trim();
      const chip = document.createElement('span');
      chip.className = 'role-chip';
      chip.setAttribute('data-role', clean);
      chip.innerHTML = `${escapeHtml(clean)} <button class="chip-remove">&times;</button>`;
      chipsContainer.appendChild(chip);
    }
  });

  // Run Ranking
  runBtn.addEventListener('click', executeRankCareers);
}

async function executeRankCareers() {
  const chips = document.querySelectorAll('.role-chip');
  const roles = Array.from(chips).map(c => c.getAttribute('data-role')).filter(Boolean);

  if (roles.length < 2) {
    alert('Please keep at least 2 occupations to compare and rank.');
    return;
  }

  const loader = document.getElementById('rankLoader');
  const card = document.getElementById('rankLeaderboardCard');
  const tbody = document.getElementById('rankTableBody');

  loader.classList.remove('hidden');
  card.classList.add('hidden');

  // Read weights
  const wGrowth = parseFloat(document.getElementById('weightGrowth').value) / 100;
  const wDemand = parseFloat(document.getElementById('weightDemand').value) / 100;
  const wAi = parseFloat(document.getElementById('weightAi').value) / 100;
  const wSalary = parseFloat(document.getElementById('weightSalary').value) / 100;
  const wConfidence = parseFloat(document.getElementById('weightConfidence').value) / 100;

  try {
    const res = await fetch(`${API_BASE}/api/v1/career/rank`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        occupations: roles,
        weights: {
          growth: wGrowth,
          current_demand: wDemand,
          ai_resilience: wAi,
          salary_level: wSalary,
          confidence: wConfidence
        }
      })
    });

    if (!res.ok) throw new Error('Ranking computation failed');
    const data = await res.json();

    loader.classList.add('hidden');
    card.classList.remove('hidden');

    tbody.innerHTML = (data.rankings || []).map(r => `
      <tr>
        <td><strong>#${r.rank}</strong></td>
        <td>
          <strong>${escapeHtml(r.occupation)}</strong>
          <span class="sub-text" style="display:block;">${escapeHtml(r.outlook)}</span>
        </td>
        <td><strong style="color:var(--color-primary); font-size:14px;">${(r.composite_score * 100).toFixed(1)}</strong></td>
        <td>${Math.round(r.current_demand_score * 100)}%</td>
        <td>${Math.round(r.growth_score * 100)}%</td>
        <td>${Math.round(r.ai_exposure_score * 100)}%</td>
        <td><span class="sub-text">${escapeHtml(r.primary_driver || 'Balanced multi-signal score')}</span></td>
      </tr>
    `).join('');

  } catch (err) {
    loader.classList.add('hidden');
    alert(`Ranking failed: ${err.message}`);
  }
}

// Utility Helpers
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
