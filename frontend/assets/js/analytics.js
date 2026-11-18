import { $, STATUS_LABEL, api, boot, fmtDate, html, logo } from "./app.js";

async function render() {
  const data = await api("/analytics");

  const max = Math.max(1, ...data.by_status.map((s) => s.count));
  $("#by-status").innerHTML = html`${data.by_status.map((s) => html`
    <div class="bar-row st-${s.status}">
      <span>${STATUS_LABEL[s.status]}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(s.count / max) * 100}%"></div></div>
      <span class="n">${s.count}</span>
    </div>`)}`.s;

  const top = Math.max(1, data.funnel[0].count);
  $("#funnel").innerHTML = html`${data.funnel.map((f) => html`
    <div class="funnel-step">
      <span>${f.label}</span>
      <div class="funnel-bar" style="width:${Math.max(8, (f.count / top) * 100)}%">${f.count}</div>
    </div>`)}`.s;
  const [, applied, interviews] = data.funnel;
  $("#rate").textContent = applied.count
    ? `${Math.round((interviews.count / applied.count) * 100)} % des candidatures mènent à un entretien`
    : "";

  const wmax = Math.max(1, ...data.weekly.map((w) => w.events));
  $("#weeks").innerHTML = html`${data.weekly.map((w) => html`
    <div class="week" title="${w.events} changement(s) la semaine du ${fmtDate(w.week)}">
      <span>${w.events || ""}</span>
      <div class="col ${w.events ? "" : "zero"}" style="height:${(w.events / wmax) * 110}px"></div>
      <span>${fmtDate(w.week)}</span>
    </div>`)}`.s;

  $("#by-company").innerHTML = html`${data.by_company.map((r) => html`
    <div class="row-item">
      ${logo(r.company, "sm")}
      <div class="grow"><span class="title">${r.company.name}</span></div>
      <span class="small muted">${r.jobs} poste${r.jobs > 1 ? "s" : ""}</span>
      <span class="pill ${r.processes ? "st-postule" : "plain"}">${r.processes} process</span>
    </div>`)}`.s;
}

boot(render);
