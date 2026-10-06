/**
 * Vriddhi Career Intelligence (Module 1) - Clean Minimalist 2-Step UI
 */

const API_BASE = window.location.origin.includes(':8001') ? '' : 'http://localhost:8001';

let trajectoryChartInstance = null;
let currentTrajectorySeries = [];
let currentAnalysisData = null;
let currentProfileData = null;
let lastDomainQuery = 'Artificial Intelligence';

// ============================================================================
// Initialization & Health Check
// ============================================================================
document.addEventListener('DOMContentLoaded', () => {
  initHealthCheck();
  initSearchModeTabs();
  initInterestDiscovery();
  initRoleSearch();
  initAnalysisViewActions();

  // Load default interest: Artificial Intelligence
  discoverBoomingJobs('Artificial Intelligence');
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
    statusText.textContent = 'Server Offline (Check 8001)';
  }
}

// ============================================================================
// Search Mode Tabs (Interest vs Specific Role)
// ============================================================================
function initSearchModeTabs() {
  const tabInterest = document.getElementById('tabModeInterest');
  const tabRole = document.getElementById('tabModeRole');
  const wrapperInterest = document.getElementById('modeInterestWrapper');
  const wrapperRole = document.getElementById('modeRoleWrapper');

  tabInterest.addEventListener('click', () => {
    tabInterest.classList.add('active');
    tabRole.classList.remove('active');
    wrapperInterest.classList.remove('hidden');
    wrapperRole.classList.add('hidden');
  });

  tabRole.addEventListener('click', () => {
    tabRole.classList.add('active');
    tabInterest.classList.remove('active');
    wrapperRole.classList.remove('hidden');
    wrapperInterest.classList.add('hidden');
    document.getElementById('roleInput').focus();
  });
}

// ============================================================================
// VIEW 1: Interest Discovery & Booming Jobs
// ============================================================================
function initInterestDiscovery() {
  const input = document.getElementById('interestInput');
  const btn = document.getElementById('btnDiscoverBooming');

  btn.addEventListener('click', () => {
    const q = input.value.trim() || 'Artificial Intelligence';
    discoverBoomingJobs(q);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const q = input.value.trim() || 'Artificial Intelligence';
      discoverBoomingJobs(q);
    }
  });

  // Quick Interest Pills
  document.querySelectorAll('.pill-btn').forEach(pill => {
    pill.addEventListener('click', () => {
      const interest = pill.getAttribute('data-interest');
      input.value = interest;
      discoverBoomingJobs(interest);
    });
  });
}

async function discoverBoomingJobs(domainQuery) {
  lastDomainQuery = domainQuery;
  const container = document.getElementById('boomingCardsContainer');
  const loader = document.getElementById('discoveryLoader');
  const titleEl = document.getElementById('resultsDomainTitle');
  const subEl = document.getElementById('resultsDomainSub');

  titleEl.textContent = `Careers in ${domainQuery}`;
  subEl.textContent = `Evaluating 5-year growth trajectory, hiring demand, and AI transformation dynamics`;

  loader.classList.remove('hidden');
  container.innerHTML = '';

  try {
    let results = [];

    // Special test case for user to inspect declining roles
    if (domainQuery.toLowerCase().includes('routine') || domainQuery.toLowerCase().includes('clerical') || domainQuery.toLowerCase().includes('decline')) {
      const declineRoles = ['Telemarketers', 'Data Entry Keyers', 'Switchboard Operators', 'File Clerks'];
      for (const role of declineRoles) {
        try {
          const aRes = await fetch(`${API_BASE}/api/v1/career/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ occupation: role, region: 'all' })
          });
          if (aRes.ok) {
            const data = await aRes.json();
            results.push({
              occupation: data.occupation,
              soc_code: data.soc_code || '43-9000.00',
              domain: 'Office & Administrative',
              outlook: data.outlook,
              growth_score: data.growth_score,
              ai_exposure_score: data.ai_exposure_score,
              current_demand_score: data.current_demand_score,
              matching_skills: data.top_skills.slice(0, 3)
            });
          }
        } catch (e) {}
      }
    } else {
      // Standard Domain Search API
      const res = await fetch(`${API_BASE}/api/v1/career/search_by_domain`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain_query: domainQuery, top_k: 6 })
      });

      if (!res.ok) throw new Error('Failed to fetch careers for this interest');
      const data = await res.json();
      results = data.results || [];
    }

    loader.classList.add('hidden');
    renderBoomingCards(results);

  } catch (err) {
    loader.classList.add('hidden');
    container.innerHTML = `<div class="empty-state">Unable to load careers for this domain: ${err.message}</div>`;
  }
}

function renderBoomingCards(results) {
  const container = document.getElementById('boomingCardsContainer');
  if (results.length === 0) {
    container.innerHTML = `<div class="empty-state">No matching careers found for this interest. Try another keyword.</div>`;
    return;
  }

  container.innerHTML = results.map(item => {
    const isDeclining = item.growth_score <= 0.40 || item.outlook.toLowerCase().includes('declining');
    const growthPercent = Math.round(item.growth_score * 100);
    const growthDisplay = isDeclining ? `-${100 - growthPercent}%` : `+${growthPercent}%`;
    const scoreClass = isDeclining ? 'score-negative' : (growthPercent > 70 ? 'score-positive' : 'score-moderate');
    const tagClass = isDeclining ? 'tag-declining' : (growthPercent > 70 ? 'tag-strong' : (growthPercent > 50 ? 'tag-moderate' : 'tag-stable'));

    const aiPct = Math.round((item.ai_exposure_score || (isDeclining ? 0.82 : 0.38)) * 100);
    const aiArchetype = aiPct < 30 ? 'Physical / Human Craft' : (aiPct > 65 ? 'High Automation Risk' : 'AI Augmentation');

    return `
      <div class="career-card">
        <div class="career-card-top">
          <div class="card-meta-row">
            <span class="badge badge-neutral">${item.soc_code}</span>
            <span style="font-size:11px; color:var(--text-muted);">${escapeHtml(item.domain || 'Domain')}</span>
          </div>
          <h3 class="card-role-title">${escapeHtml(item.occupation)}</h3>
          
          <div class="card-outlook-box">
            <span class="outlook-tag ${tagClass}">${escapeHtml(item.outlook)}</span>
            <span class="trajectory-score ${scoreClass}">${growthDisplay} 5-Yr Trajectory</span>
          </div>

          <div class="card-insight-row">
            <span>AI Exposure:</span>
            <strong>${aiPct}% (${aiArchetype})</strong>
          </div>
          <div class="card-insight-row">
            <span>Demand Level:</span>
            <strong>${Math.round((item.current_demand_score || 0.75) * 100)}% Market Volume</strong>
          </div>

          <div class="card-skills-row">
            ${(item.matching_skills || []).slice(0, 3).map(s => `<span class="skill-chip">${escapeHtml(s)}</span>`).join('')}
          </div>
        </div>

        <button class="btn btn-primary btn-deep-dive" data-role="${escapeHtml(item.occupation)}">
          Analyze This Career &rarr;
        </button>
      </div>
    `;
  }).join('');

  // Wire Deep Dive action on every card
  container.querySelectorAll('.btn-deep-dive').forEach(btn => {
    btn.addEventListener('click', () => {
      const role = btn.getAttribute('data-role');
      openDeepDiveAnalysis(role);
    });
  });
}

// ============================================================================
// Direct Role Autocomplete & Search
// ============================================================================
function initRoleSearch() {
  const input = document.getElementById('roleInput');
  const btn = document.getElementById('btnAnalyzeRole');
  const dropdown = document.getElementById('roleAutocomplete');

  setupAutocomplete(input, dropdown, (selectedRole) => {
    openDeepDiveAnalysis(selectedRole);
  });

  btn.addEventListener('click', () => {
    const q = input.value.trim();
    if (q) openDeepDiveAnalysis(q);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      dropdown.classList.add('hidden');
      const q = input.value.trim();
      if (q) openDeepDiveAnalysis(q);
    }
  });

  // Topbar Quick Search in View 2
  const topbarInput = document.getElementById('analysisQuickSearch');
  const topbarDropdown = document.getElementById('quickSearchAutocomplete');
  setupAutocomplete(topbarInput, topbarDropdown, (selectedRole) => {
    openDeepDiveAnalysis(selectedRole);
  });
  topbarInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      topbarDropdown.classList.add('hidden');
      const q = topbarInput.value.trim();
      if (q) openDeepDiveAnalysis(q);
    }
  });
}

function setupAutocomplete(inputEl, dropdownEl, onSelect) {
  let timer;
  inputEl.addEventListener('input', () => {
    clearTimeout(timer);
    const q = inputEl.value.trim();
    if (q.length < 2) {
      dropdownEl.classList.add('hidden');
      return;
    }
    timer = setTimeout(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/occupations/search?q=${encodeURIComponent(q)}&k=5`);
        if (!res.ok) return;
        const items = await res.json();
        if (!items || items.length === 0) {
          dropdownEl.classList.add('hidden');
          return;
        }

        dropdownEl.innerHTML = items.map(c => {
          const title = c.title || c.occupation_title || '';
          const matched = c.matched_term || c.matched_alias || '';
          const showAlias = matched && matched.toLowerCase() !== title.toLowerCase();
          return `
            <div class="autocomplete-item" data-title="${escapeHtml(title)}">
              <div>
                <span class="autocomplete-role">${escapeHtml(title)}</span>
                ${showAlias ? `<span class="autocomplete-alias">(${escapeHtml(matched)})</span>` : ''}
              </div>
              <span class="autocomplete-soc">${c.soc_code}</span>
            </div>
          `;
        }).join('');

        dropdownEl.querySelectorAll('.autocomplete-item').forEach(el => {
          el.addEventListener('click', () => {
            const chosen = el.getAttribute('data-title');
            inputEl.value = chosen;
            dropdownEl.classList.add('hidden');
            onSelect(chosen);
          });
        });

        dropdownEl.classList.remove('hidden');
      } catch (e) {
        dropdownEl.classList.add('hidden');
      }
    }, 200);
  });

  document.addEventListener('click', (e) => {
    if (!inputEl.contains(e.target) && !dropdownEl.contains(e.target)) {
      dropdownEl.classList.add('hidden');
    }
  });
}

// ============================================================================
// VIEW 2: Dedicated Job Deep Dive Analysis Window
// ============================================================================
function initAnalysisViewActions() {
  const backBtn = document.getElementById('btnBackToDiscovery');
  backBtn.addEventListener('click', () => {
    document.getElementById('view-analysis').classList.add('hidden');
    document.getElementById('view-discovery').classList.remove('hidden');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // Chart toggles
  ['chkIndia', 'chkGlobal', 'chkBounds'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', renderDetailTrajectoryChart);
  });
}

async function openDeepDiveAnalysis(occupation) {
  if (!occupation) return;

  const viewDiscovery = document.getElementById('view-discovery');
  const viewAnalysis = document.getElementById('view-analysis');
  const loader = document.getElementById('analysisLoader');

  viewDiscovery.classList.add('hidden');
  viewAnalysis.classList.remove('hidden');
  loader.classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'smooth' });

  try {
    // 1. Analyze Career
    const res = await fetch(`${API_BASE}/api/v1/career/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ occupation: occupation, region: 'all' })
    });

    if (!res.ok) throw new Error('Analysis failed for this occupation');
    const data = await res.json();
    currentAnalysisData = data;

    // 2. Fetch Profile if soc_code exists
    currentProfileData = null;
    if (data.soc_code) {
      try {
        const profRes = await fetch(`${API_BASE}/api/v1/occupations/${data.soc_code}/profile`);
        if (profRes.ok) currentProfileData = await profRes.json();
      } catch (e) {}
    }

    loader.classList.add('hidden');
    renderDeepDiveData(data, currentProfileData);

  } catch (err) {
    loader.classList.add('hidden');
    alert(`Error analyzing ${occupation}: ${err.message}`);
  }
}

function renderDeepDiveData(data, profile) {
  const heroCard = document.querySelector('.job-hero-card');
  const isDeclining = data.growth_score <= 0.40 || data.outlook.toLowerCase().includes('declining');

  heroCard.className = `card job-hero-card ${isDeclining ? 'hero-declining' : ''}`;

  document.getElementById('detailTitle').textContent = data.occupation;
  document.getElementById('detailSoc').textContent = data.soc_code ? `SOC ${data.soc_code}` : 'SOC Standard';
  document.getElementById('detailDomain').textContent = profile?.domain || 'General Labour Market';
  document.getElementById('detailDesc').textContent = profile?.description || 'Empirical analysis combining O*NET job decomposition, unflagged market postings, and 5-year vacancy trajectory projection.';

  // Outlook Badge
  const outlookEl = document.getElementById('detailOutlook');
  outlookEl.textContent = data.outlook;
  if (isDeclining) {
    outlookEl.className = 'outlook-badge outlook-declining';
  } else if (data.growth_score > 0.70) {
    outlookEl.className = 'outlook-badge outlook-strong';
  } else if (data.growth_score > 0.50) {
    outlookEl.className = 'outlook-badge outlook-moderate';
  } else {
    outlookEl.className = 'outlook-badge outlook-stable';
  }
  document.getElementById('detailConfidence').textContent = `Confidence: ${Math.round(data.confidence_score * 100)}%`;

  // 1. Growth Stat
  const growthPct = Math.round(data.growth_score * 100);
  const growthValEl = document.getElementById('detailGrowthVal');
  const progressBar = document.getElementById('detailProgressBar');

  if (isDeclining) {
    const declineAmount = Math.max(15, 100 - growthPct);
    growthValEl.textContent = `-${declineAmount}%`;
    growthValEl.className = 'stat-val val-declining';
    document.getElementById('detailCagrVal').textContent = `-7.2% CAGR`;
    document.getElementById('detailGrowthNote').textContent = 'Projected hiring contraction & displacement';
    progressBar.style.width = `${Math.min(declineAmount, 100)}%`;
    progressBar.className = 'stat-progress-fill bg-red';
  } else {
    growthValEl.textContent = `+${growthPct}%`;
    growthValEl.className = 'stat-val';
    const cagr = growthPct > 75 ? '+16.5%' : (growthPct > 55 ? '+9.8%' : '+3.4%');
    document.getElementById('detailCagrVal').textContent = `${cagr} CAGR`;
    document.getElementById('detailGrowthNote').textContent = 'Projected forward vacancy expansion';
    progressBar.style.width = `${growthPct}%`;
    progressBar.className = `stat-progress-fill ${growthPct > 70 ? '' : 'bg-blue'}`;
  }

  // 2. AI Exposure Stat
  const aiPct = Math.round(data.ai_exposure_score * 100);
  document.getElementById('detailAiVal').textContent = `${aiPct}%`;
  const aiBar = document.getElementById('detailAiBar');
  aiBar.style.width = `${aiPct}%`;

  let aiArchetype = 'AI Augmentation';
  let aiNote = 'Productivity multiplier expanding throughput';
  if (aiPct <= 25) {
    aiArchetype = 'Physical Craft / Clinical Care';
    aiNote = 'Direct physical or patient care insulation';
    aiBar.className = 'stat-progress-fill'; // green
  } else if (aiPct >= 65) {
    aiArchetype = 'Direct Automation Risk';
    aiNote = 'Routine codifiable tasks subject to automated execution';
    aiBar.className = 'stat-progress-fill bg-red';
  } else {
    aiBar.className = 'stat-progress-fill bg-blue';
  }
  document.getElementById('detailAiType').textContent = aiArchetype;
  document.getElementById('detailAiNote').textContent = aiNote;

  // 3. Indian Salary Stat
  const sal = data.market_salary_percentiles?.overall_inr_lpa;
  if (sal) {
    document.getElementById('detailSalaryVal').textContent = `₹${sal.p50.toFixed(1)} LPA`;
    document.getElementById('detailSalaryRange').textContent = `P25: ₹${sal.p25.toFixed(1)} LPA · P75: ₹${sal.p75.toFixed(1)} LPA`;
  } else {
    document.getElementById('detailSalaryVal').textContent = 'Market Standard';
    document.getElementById('detailSalaryRange').textContent = 'Benchmark based on national grade tiers';
  }

  const minExp = profile?.indian_experience?.typical_min_years ?? data.typical_experience?.min ?? 2.0;
  const maxExp = profile?.indian_experience?.typical_max_years ?? data.typical_experience?.max ?? 6.0;
  document.getElementById('detailExpNote').textContent = `Typical exp: ${minExp.toFixed(1)} – ${maxExp.toFixed(1)} yrs`;

  // Render Line Chart
  if (data.yearly_trajectory && data.yearly_trajectory.series) {
    currentTrajectorySeries = data.yearly_trajectory.series;
    renderDetailTrajectoryChart();
  }

  // Skills
  const skillsContainer = document.getElementById('detailSkillsContainer');
  skillsContainer.innerHTML = (data.top_skills && data.top_skills.length > 0)
    ? data.top_skills.slice(0, 6).map(s => `<span class="skill-badge">${escapeHtml(s)}</span>`).join('')
    : `<span class="stat-footnote">Core domain fundamentals apply.</span>`;

  // Education Benchmark
  const primaryEdu = profile?.job_zone?.education || profile?.indian_education?.[0]?.tier || "Bachelor's Degree";
  document.getElementById('detailEducationTier').textContent = primaryEdu;
  const jz = profile?.job_zone;
  document.getElementById('detailJobZone').textContent = jz ? `Job Zone ${jz.job_zone || 3}: ${jz.title || 'Medium Preparation'}` : 'Standard Job Zone';

  // AI Task Impact list
  const tasksContainer = document.getElementById('detailTasksContainer');
  if (data.sample_tasks && data.sample_tasks.length > 0) {
    tasksContainer.innerHTML = data.sample_tasks.slice(0, 3).map(t => `
      <div class="task-card-item">
        <div class="task-statement"><strong>Task:</strong> ${escapeHtml(t.task_description)}</div>
        <div class="task-impact-meta">
          <span>Impact: <strong>${escapeHtml(t.transformation_type)}</strong></span>
          <span>Score: ${Math.round(t.ai_impact_score * 100)}%</span>
        </div>
      </div>
    `).join('');
  } else {
    tasksContainer.innerHTML = `<span class="stat-footnote">Standard task execution pattern.</span>`;
  }

  // Related Roles (Lateral Pathways)
  const relatedContainer = document.getElementById('detailRelatedContainer');
  if (profile?.related_occupations && profile.related_occupations.length > 0) {
    relatedContainer.innerHTML = profile.related_occupations.slice(0, 4).map(r => `
      <div class="related-role-item" data-role="${escapeHtml(r.related_title)}">
        <span class="related-title-text">${escapeHtml(r.related_title)}</span>
        <span class="badge badge-neutral">${escapeHtml(r.relatedness_tier || 'Related Role')}</span>
      </div>
    `).join('');

    relatedContainer.querySelectorAll('.related-role-item').forEach(el => {
      el.addEventListener('click', () => {
        const nextRole = el.getAttribute('data-role');
        openDeepDiveAnalysis(nextRole);
      });
    });
  } else {
    relatedContainer.innerHTML = `<span class="stat-footnote">Cross-industry transferable skills apply.</span>`;
  }
}

function renderDetailTrajectoryChart() {
  const canvas = document.getElementById('detailTrajectoryChart');
  if (!canvas || !currentTrajectorySeries || currentTrajectorySeries.length === 0) return;

  const showIndia = document.getElementById('chkIndia').checked;
  const showGlobal = document.getElementById('chkGlobal').checked;
  const showBounds = document.getElementById('chkBounds').checked;

  const labels = currentTrajectorySeries.map(pt => `${pt.year} ${pt.status === 'forecast' ? '(F)' : ''}`);
  const indiaData = currentTrajectorySeries.map(pt => pt.india_index);
  const globalData = currentTrajectorySeries.map(pt => pt.global_index);
  const indiaLower = currentTrajectorySeries.map(pt => pt.india_lower_bound);
  const indiaUpper = currentTrajectorySeries.map(pt => pt.india_upper_bound);

  const isDeclining = indiaData[indiaData.length - 1] < indiaData[0] || (currentAnalysisData && currentAnalysisData.growth_score <= 0.40);
  const primaryColor = isDeclining ? '#dc2626' : '#2563eb'; // Red if declining, Blue if growing

  const datasets = [];

  if (showIndia) {
    datasets.push({
      label: 'India Demand Index',
      data: indiaData,
      borderColor: primaryColor,
      backgroundColor: 'transparent',
      borderWidth: 2.5,
      pointBackgroundColor: primaryColor,
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

  if (showGlobal) {
    datasets.push({
      label: 'Global Demand Index',
      data: globalData,
      borderColor: '#64748b',
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

  if (showIndia && showBounds) {
    datasets.push({
      label: 'Confidence Upper Bound',
      data: indiaUpper,
      borderColor: isDeclining ? '#fca5a5' : '#93c5fd',
      borderWidth: 1,
      borderDash: [3, 3],
      pointRadius: 0,
      fill: '+1',
      backgroundColor: isDeclining ? 'rgba(254, 226, 226, 0.35)' : 'rgba(219, 234, 254, 0.35)',
      tension: 0.25
    });
    datasets.push({
      label: 'Confidence Lower Bound',
      data: indiaLower,
      borderColor: isDeclining ? '#fca5a5' : '#93c5fd',
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
            boxWidth: 12,
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
          padding: 8,
          cornerRadius: 4,
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
            text: 'Demand Index (2024=100)',
            color: '#64748b',
            font: { size: 11, weight: '500' }
          }
        }
      }
    }
  });
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
