(() => {
  "use strict";

  const REFRESH_MS = 60000;
  const dashboard = document.getElementById("dashboard");
  const drawer = document.getElementById("drawer");
  const drawerBody = document.getElementById("drawer-body");
  const scrim = document.getElementById("scrim");
  const refreshBtn = document.getElementById("refresh-btn");
  const addBtn = document.getElementById("add-btn");
  const statusEl = document.getElementById("refresh-status");
  const confirmDialog = document.getElementById("confirm-dialog");

  let currentTab = "active";
  try { currentTab = localStorage.getItem("bets.tab") || "active"; } catch (_) { /* storage unavailable */ }
  let inFlight = false;
  let lastRefresh = 0;
  let openerId = null;

  // --- formatting -------------------------------------------------------------

  const dateFmt = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });
  const stampFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const clockFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

  function formatStamp(d) {
    return d.toDateString() === new Date().toDateString() ? clockFmt.format(d) : stampFmt.format(d);
  }

  function formatDates(root) {
    root.querySelectorAll("time[data-date]").forEach((el) => {
      const [y, m, d] = el.dataset.date.split("-").map(Number);
      if (y && m && d) el.textContent = dateFmt.format(new Date(y, m - 1, d));
    });
    root.querySelectorAll("time[data-ts]").forEach((el) => {
      const d = new Date(el.dataset.ts);
      if (!isNaN(d)) {
        el.textContent = formatStamp(d);
        el.title = d.toLocaleString();
      }
    });
  }

  function trimNumber(x, places) {
    return x.toFixed(places).replace(/\.?0+$/, "");
  }

  function parseNum(text) {
    const cleaned = String(text || "").replace(/[,%\s]/g, "");
    if (cleaned === "") return null;
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
  }

  function uid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "id-" + Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  // --- confirmation (every page) -------------------------------------------------------------

  function confirmAction({ title, message, button }) {
    confirmDialog.querySelector("#confirm-title").textContent = title;
    confirmDialog.querySelector("[data-confirm-message]").textContent = message;
    confirmDialog.querySelector("[data-confirm-button]").textContent = button;
    confirmDialog.returnValue = "";
    confirmDialog.showModal();
    return new Promise((resolve) => {
      confirmDialog.addEventListener("close", () => resolve(confirmDialog.returnValue === "confirm"), { once: true });
    });
  }

  // Plain forms that need a confirmation step, such as deleting a player.
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!form.dataset.confirmTitle) return;
    e.preventDefault();
    const { confirmTitle: title, confirmMessage: message, confirmButton: button } = form.dataset;
    if (await confirmAction({ title, message, button })) form.submit();
    else form.querySelector('button[type="submit"]').focus();
  });

  formatDates(document);
  if (!dashboard) return; // the rest drives the bets dashboard

  // --- tabs -------------------------------------------------------------

  function applyTab() {
    const tabs = dashboard.querySelectorAll('[role="tab"]');
    if (![...tabs].some((t) => t.dataset.tab === currentTab)) currentTab = "active";
    tabs.forEach((tab) => {
      const selected = tab.dataset.tab === currentTab;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !selected;
    });
  }

  function selectTab(name, focus) {
    currentTab = name;
    try { localStorage.setItem("bets.tab", name); } catch (_) { /* ignore */ }
    applyTab();
    if (focus) dashboard.querySelector(`[data-tab="${name}"]`).focus();
  }

  // --- dashboard and refresh -------------------------------------------------------------

  function setDashboard(html) {
    const focusedId = dashboard.contains(document.activeElement) ? document.activeElement.id : null;
    dashboard.innerHTML = html;
    formatDates(dashboard);
    applyTab();
    updateStatus();
    if (focusedId) {
      const again = document.getElementById(focusedId);
      if (again) again.focus();
    }
  }

  function updateStatus() {
    const meta = document.getElementById("refresh-meta");
    if (!meta || !meta.dataset.at) return;
    const when = formatStamp(new Date(meta.dataset.at));
    const failed = Boolean(meta.dataset.errors);
    statusEl.textContent = failed ? `Update failed · ${when}` : `Updated ${when}`;
    statusEl.title = meta.dataset.errors || "";
    statusEl.classList.toggle("failed", failed);
  }

  async function refresh() {
    if (inFlight) return;
    inFlight = true;
    refreshBtn.disabled = true;
    statusEl.classList.remove("failed");
    statusEl.textContent = "Updating…";
    try {
      const response = await fetch("/refresh", { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setDashboard(await response.text());
    } catch (err) {
      statusEl.textContent = "Update failed";
      statusEl.title = String(err);
      statusEl.classList.add("failed");
    } finally {
      inFlight = false;
      refreshBtn.disabled = false;
      lastRefresh = Date.now();
    }
  }

  function refreshIfDue() {
    if (!document.hidden && Date.now() - lastRefresh >= REFRESH_MS) refresh();
  }

  // --- menus -------------------------------------------------------------

  function closeMenus(except) {
    document.querySelectorAll("details.menu[open]").forEach((menu) => {
      if (menu !== except) menu.open = false;
    });
  }

  document.addEventListener("click", (e) => {
    const summary = e.target.closest("details.menu > summary");
    closeMenus(summary ? summary.parentElement : null);
  });

  // --- drawer -------------------------------------------------------------

  function openerFor(trigger) {
    const menu = trigger.closest("details.menu");
    return menu ? menu.querySelector("summary").id : trigger.id;
  }

  async function openDrawer(url, trigger) {
    openerId = trigger ? openerFor(trigger) : null;
    closeMenus();
    drawer.hidden = false;
    scrim.hidden = false;
    document.body.style.overflow = "hidden";
    drawerBody.innerHTML = '<p class="drawer-loading">Loading…</p>';
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setDrawer(await response.text(), false);
    } catch (err) {
      drawerBody.innerHTML = '<p class="drawer-loading">Could not load the form. <button type="button" class="link-btn" data-close>Close</button></p>';
    }
  }

  function setDrawer(html, focusErrors) {
    drawerBody.innerHTML = html;
    formatDates(drawerBody);
    const form = drawerBody.querySelector("form");
    if (!form) return;
    initForm(form);
    let target = null;
    if (focusErrors) {
      const invalid = form.querySelector('[aria-invalid="true"]:not([disabled])');
      const badRow = form.querySelector('.bettor[data-has-error] input:not([type="hidden"])');
      // Focus whichever problem comes first in the form.
      target = invalid && badRow
        ? (invalid.compareDocumentPosition(badRow) & Node.DOCUMENT_POSITION_FOLLOWING ? invalid : badRow)
        : invalid || badRow;
      if (!target) {
        target = form.querySelector('[role="alert"]');
        if (target) target.tabIndex = -1;
      }
      const details = target && target.closest("details");
      if (details) details.open = true;
    }
    target = target || form.querySelector('input:not([type="hidden"]):not([disabled]), select, textarea, button[type="submit"]');
    if (target) target.focus();
  }

  function closeDrawer() {
    if (drawer.hidden) return;
    drawer.hidden = true;
    scrim.hidden = true;
    document.body.style.overflow = "";
    drawerBody.innerHTML = "";
    const back = (openerId && document.getElementById(openerId)) || addBtn;
    back.focus();
  }

  drawer.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const focusable = [...drawer.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary'
    )].filter((el) => el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });

  drawerBody.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = new FormData(form);
    if (e.submitter && e.submitter.name) data.set(e.submitter.name, e.submitter.value);
    const buttons = form.querySelectorAll('button[type="submit"]');
    buttons.forEach((b) => { b.disabled = true; });
    try {
      // getAttribute: a submit button named "action" shadows form.action.
      const response = await fetch(form.getAttribute("action"), { method: "POST", body: data });
      const html = await response.text();
      if (response.ok) {
        setDashboard(html);
        closeDrawer();
      } else if (response.status === 422) {
        setDrawer(html, true);
      } else {
        throw new Error(`HTTP ${response.status}`);
      }
    } catch (err) {
      buttons.forEach((b) => { b.disabled = false; });
      let alert = form.querySelector(".form-alert");
      if (!alert) {
        alert = document.createElement("p");
        alert.className = "form-alert";
        alert.setAttribute("role", "alert");
        form.querySelector(".drawer-head").after(alert);
      }
      alert.textContent = `Could not save (${err.message}). Your entries are still here.`;
    }
  });

  // --- global clicks and keys -------------------------------------------------------------

  document.addEventListener("click", (e) => {
    const drawerTrigger = e.target.closest("[data-drawer]");
    if (drawerTrigger) {
      openDrawer(drawerTrigger.dataset.drawer, drawerTrigger);
      return;
    }
    if (e.target.closest("[data-close]")) {
      closeDrawer();
      return;
    }
    const del = e.target.closest("[data-delete]");
    if (del) {
      closeMenus();
      deleteBet(del);
      return;
    }
    const tab = e.target.closest('[role="tab"]');
    if (tab) selectTab(tab.dataset.tab, false);
  });

  dashboard.addEventListener("keydown", (e) => {
    const tab = e.target.closest('[role="tab"]');
    if (!tab || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    selectTab(tab.dataset.tab === "active" ? "completed" : "active", true);
  });

  async function deleteBet(trigger) {
    const opener = openerFor(trigger);
    const confirmed = await confirmAction({
      title: `Delete “${trigger.dataset.title}”?`,
      message: "This removes the bet and its positions. Results already logged on the leaderboard are kept.",
      button: "Delete bet",
    });
    if (!confirmed) {
      const back = document.getElementById(opener);
      if (back) back.focus();
      return;
    }
    try {
      const response = await fetch(trigger.dataset.delete, { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setDashboard(await response.text());
    } catch (err) {
      statusEl.textContent = "Delete failed";
      statusEl.classList.add("failed");
    }
    const tab = dashboard.querySelector('[role="tab"][aria-selected="true"]');
    (tab || addBtn).focus();
  }

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || confirmDialog.open) return;
    const menu = document.querySelector("details.menu[open]");
    if (menu) {
      menu.open = false;
      menu.querySelector("summary").focus();
      return;
    }
    if (!drawer.hidden) closeDrawer();
  });

  refreshBtn.addEventListener("click", refresh);

  // --- forms -------------------------------------------------------------

  function initForm(form) {
    wireProbabilities(form);
    if (form.dataset.form === "bet") wireBetForm(form);
  }

  function wireProbabilities(form) {
    const update = () => {
      const yes = form.querySelector('input[name="prob_yes"]');
      const no = form.querySelector("[data-prob-no]");
      if (yes && no) {
        const v = parseNum(yes.value);
        no.textContent = v === null || v < 0 || v > 100 ? "—" : `${trimNumber(100 - v, 4)}%`;
      }
      const total = form.querySelector("[data-prob-total]");
      if (total) {
        const values = [...form.querySelectorAll("input[data-outcome]")].map((i) => parseNum(i.value));
        const filled = values.filter((v) => v !== null);
        if (!filled.length) {
          total.textContent = "—";
          total.classList.remove("off");
        } else {
          const sum = filled.reduce((a, b) => a + b, 0);
          total.textContent = `${trimNumber(sum, 4)}%`;
          total.classList.toggle("off", Math.abs(sum - 100) > 0.01 || filled.length !== values.length);
        }
      }
    };
    form.addEventListener("input", (e) => {
      if (e.target.matches('input[name^="prob_"]')) update();
    });
    form.updateProbabilities = update;
    update();
  }

  function wireBetForm(form) {
    const typeInputs = form.querySelectorAll('input[name="type"]');
    if (!typeInputs.length) return; // settled bet: metadata only

    const outcomeSection = form.querySelector('[data-section="outcomes"]');
    const outcomeRows = form.querySelector("[data-outcome-rows]");
    const pmSection = form.querySelector('[data-section="polymarket"]');
    const probSection = form.querySelector('[data-section="probabilities"]');
    const probFieldset = form.querySelector("[data-probs-fieldset]");
    const probBinary = form.querySelector("[data-probs-binary]");
    const probMc = form.querySelector("[data-probs-mc]");
    const probMcRows = form.querySelector("[data-probs-mc-rows]");
    const bettorRows = form.querySelector("[data-bettor-rows]");
    const bettorTpl = form.querySelector('template[data-tpl="bettor"]');
    const pmJson = form.querySelector("[data-pm-json]");
    const pmUrl = form.querySelector("#f-pm-url");
    const pmStatus = form.querySelector("[data-pm-status]");
    const pmCandidates = form.querySelector("[data-pm-candidates]");
    const pmSelected = form.querySelector("[data-pm-selected]");
    const symbol = form.dataset.currency || "";
    const moneyFmt = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    const binaryHint = form.querySelector("[data-binary-hint]");
    const type = () => form.querySelector('input[name="type"]:checked').value;
    const money = (x) => symbol + moneyFmt.format(x);
    const linked = () => Boolean(pmJson.value);

    function showSection(el, show) {
      el.hidden = !show;
      if ("disabled" in el) el.disabled = !show;
    }

    function outcomes() {
      if (type() === "binary") return [{ id: "yes", label: "Yes" }, { id: "no", label: "No" }];
      return [...outcomeRows.querySelectorAll("[data-outcome-row]")].map((row, i) => ({
        id: row.querySelector('input[name="outcome_id"]').value,
        label: row.querySelector('input[name="outcome_label"]').value.trim() || `Outcome ${i + 1}`,
      }));
    }

    function rebuildPicks() {
      const list = outcomes();
      form.querySelectorAll("[data-pick]").forEach((select) => {
        const previous = select.value || select.dataset.selected;
        select.replaceChildren(new Option("Choose…", ""), ...list.map((o) => new Option(o.label, o.id)));
        select.value = list.some((o) => o.id === previous) ? previous : "";
        select.dataset.selected = select.value;
      });
    }

    function rebuildMcProbabilities() {
      const values = {};
      probMcRows.querySelectorAll("input[data-outcome]").forEach((input) => { values[input.dataset.outcome] = input.value; });
      const disabled = type() !== "multiple_choice";
      probMcRows.replaceChildren(...outcomes().map((o) => {
        const row = document.createElement("div");
        row.className = "prob-row";
        const label = document.createElement("label");
        label.htmlFor = `p-${o.id}`;
        label.textContent = o.label;
        const wrap = document.createElement("span");
        wrap.className = "pct-input";
        const input = document.createElement("input");
        Object.assign(input, { id: `p-${o.id}`, name: `prob_${o.id}`, inputMode: "decimal", value: values[o.id] || "", disabled });
        input.dataset.outcome = o.id;
        const pct = document.createElement("span");
        pct.textContent = "%";
        wrap.append(input, pct);
        row.append(label, wrap);
        return row;
      }));
      form.updateProbabilities();
    }

    function syncOutcomeButtons() {
      const rows = outcomeRows.querySelectorAll("[data-outcome-row]");
      rows.forEach((row) => { row.querySelector("[data-remove-outcome]").disabled = rows.length <= 3; });
    }

    function syncBettorButtons() {
      const rows = bettorRows.querySelectorAll("[data-bettor-row]");
      rows.forEach((row) => { row.querySelector("[data-remove-bettor]").disabled = rows.length <= 2; });
    }

    function syncProbabilityMode() {
      const mc = type() === "multiple_choice";
      showSection(probSection, !linked());
      probFieldset.disabled = linked();
      probBinary.hidden = mc;
      probBinary.querySelectorAll("input").forEach((i) => { i.disabled = mc; });
      probMc.hidden = !mc;
      if (probMcRows) probMcRows.querySelectorAll("input").forEach((i) => { i.disabled = !mc; });
    }

    function setType() {
      const mc = type() === "multiple_choice";
      showSection(outcomeSection, mc);
      showSection(pmSection, !mc);
      if (mc && linked()) clearLink();
      syncProbabilityMode();
      rebuildPicks();
      rebuildMcProbabilities();
      binaryHint.hidden = mc;
      form.querySelectorAll("[data-amount-label]").forEach((label) => { label.textContent = mc ? "Stake" : "Bet amount"; });
      bettorRows.querySelectorAll("[data-bettor-row]").forEach(updatePayout);
    }

    // Outcome rows
    function addOutcome() {
      const id = uid();
      const index = outcomeRows.children.length + 1;
      const row = document.createElement("div");
      row.className = "outcome-row";
      row.dataset.outcomeRow = "";
      row.innerHTML =
        '<input type="hidden" name="outcome_id">' +
        '<label class="sr-only"></label>' +
        '<input name="outcome_label" autocomplete="off">' +
        '<button type="button" class="icon-btn" data-remove-outcome aria-label="Remove outcome">' +
        '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>';
      row.querySelector('input[type="hidden"]').value = id;
      const label = row.querySelector("label");
      label.htmlFor = `o-${id}`;
      label.textContent = `Outcome ${index}`;
      const input = row.querySelector('input[name="outcome_label"]');
      input.id = `o-${id}`;
      input.placeholder = `Outcome ${index}`;
      outcomeRows.append(row);
      syncOutcomeButtons();
      rebuildPicks();
      rebuildMcProbabilities();
      input.focus();
    }

    // Bettor rows
    function entryProbability(row) {
      const v = parseNum(row.querySelector("[data-value]").value);
      if (v === null) return null;
      if (row.querySelector("[data-format]").value === "probability") return v > 0 && v < 100 ? v / 100 : null;
      return v > 1 ? 1 / v : null;
    }

    function updatePayout(row) {
      const amount = parseNum(row.querySelector("[data-amount]").value);
      const q = entryProbability(row);
      const valid = amount !== null && amount > 0 && q !== null;
      const binary = type() === "binary";
      row.querySelector("[data-payout-label]").textContent = binary ? "Risks" : "Win payout";
      // Binary: the amount is what the winner collects, and the stake is its share at q.
      row.querySelector("[data-payout]").textContent = !valid ? "—"
        : binary ? `${money(amount * q)} to win ${money(amount * (1 - q))}` : money(amount / q);
    }

    // In a two-person binary bet, the other bettor takes the opposite side for the
    // same amount at the complementary probability. Editing either row updates the other.
    function matchOtherSide(row, changed) {
      if (type() !== "binary") return;
      const rows = [...bettorRows.querySelectorAll("[data-bettor-row]")];
      if (rows.length !== 2) return;
      const other = rows[rows[0] === row ? 1 : 0];
      const pick = row.querySelector("[data-pick]").value;
      const otherPick = other.querySelector("[data-pick]");
      if (changed === "pick" && pick) {
        otherPick.value = pick === "yes" ? "no" : "yes";
        otherPick.dataset.selected = otherPick.value;
      }
      if (!pick || otherPick.value === pick) return;
      if (changed === "amount") {
        other.querySelector("[data-amount]").value = row.querySelector("[data-amount]").value;
      } else {
        const q = entryProbability(row);
        if (q !== null) {
          const isPct = other.querySelector("[data-format]").value === "probability";
          other.querySelector("[data-value]").value = trimNumber(isPct ? (1 - q) * 100 : 1 / (1 - q), 10);
        }
      }
      updatePayout(other);
    }

    function switchFormat(row) {
      const isPct = row.querySelector("[data-format]").value === "probability";
      const input = row.querySelector("[data-value]");
      const v = parseNum(input.value);
      if (v !== null && v > 0) {
        // Convert to the equivalent representation with enough precision to keep the payout.
        const converted = isPct ? (v > 1 ? 100 / v : null) : (v < 100 ? 100 / v : null);
        if (converted !== null) input.value = trimNumber(converted, 10);
      }
      row.querySelector("[data-value-wrap]").classList.toggle("is-pct", isPct);
      row.querySelector(`label[for="${input.id}"]`).textContent = isPct ? "Probability" : "Odds";
      updatePayout(row);
    }

    function addBettor() {
      const holder = document.createElement("div");
      holder.innerHTML = bettorTpl.innerHTML.replaceAll("__i__", uid());
      const row = holder.firstElementChild;
      bettorRows.append(row);
      syncBettorButtons();
      rebuildPicks();
      updatePayout(row);
      row.querySelector('input[name="pos_name"]').focus();
    }

    // Polymarket link
    function fmtPct(x) {
      return `${trimNumber(Number(x) * 100, 1)}%`;
    }

    function selectCandidate(c) {
      const { midpoints, ...link } = c;
      pmJson.value = JSON.stringify(link);
      pmUrl.value = c.url;
      pmCandidates.replaceChildren();
      pmStatus.textContent = "";
      pmStatus.classList.remove("error");
      const a = pmSelected.querySelector("[data-pm-link]");
      a.href = c.url;
      a.textContent = c.question;
      pmSelected.querySelector("[data-pm-prices]").textContent = midpoints
        ? ` · Yes ${fmtPct(midpoints.yes)} · No ${fmtPct(midpoints.no)}`
        : " · No current midpoint";
      pmSelected.hidden = false;
      const title = form.querySelector("#f-title");
      if (!title.value.trim()) title.value = c.question;
      const expiry = form.querySelector("#f-expiry");
      if (!expiry.value && c.end_date) expiry.value = c.end_date;
      syncProbabilityMode();
    }

    function clearLink() {
      pmJson.value = "";
      pmUrl.value = "";
      pmSelected.hidden = true;
      pmCandidates.replaceChildren();
      pmStatus.textContent = "";
      syncProbabilityMode();
    }

    async function resolveLink(button) {
      const url = pmUrl.value.trim();
      pmStatus.classList.remove("error");
      pmCandidates.replaceChildren();
      if (!url) {
        pmStatus.textContent = "Paste a Polymarket link first.";
        pmStatus.classList.add("error");
        return;
      }
      pmStatus.textContent = "Looking up market…";
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        if (data.explicit || data.candidates.length === 1) {
          selectCandidate(data.candidates[0]);
          return;
        }
        pmStatus.textContent = `“${data.event_title}” has ${data.candidates.length} Yes/No markets. Choose the one that matches your bet:`;
        pmCandidates.replaceChildren(...data.candidates.map((c) => {
          const label = document.createElement("label");
          label.className = "pm-option";
          const radio = document.createElement("input");
          radio.type = "radio";
          radio.name = "pm_choice";
          radio.addEventListener("change", () => selectCandidate(c));
          const text = document.createElement("span");
          text.textContent = c.question;
          const detail = document.createElement("span");
          detail.className = "sub";
          detail.textContent = (c.midpoints ? `Yes ${fmtPct(c.midpoints.yes)} · No ${fmtPct(c.midpoints.no)}` : "No current midpoint")
            + (c.closed ? " · closed" : "");
          text.append(detail);
          label.append(radio, text);
          return label;
        }));
        pmCandidates.querySelector("input").focus();
      } catch (err) {
        pmStatus.textContent = err.message;
        pmStatus.classList.add("error");
      } finally {
        button.disabled = false;
      }
    }

    // Events
    form.addEventListener("change", (e) => {
      if (e.target.name === "type") setType();
      if (e.target.matches("[data-format]")) switchFormat(e.target.closest("[data-bettor-row]"));
      if (e.target.matches("[data-pick]")) {
        e.target.dataset.selected = e.target.value;
        matchOtherSide(e.target.closest("[data-bettor-row]"), "pick");
      }
    });

    form.addEventListener("input", (e) => {
      if (e.target.name === "outcome_label") {
        rebuildPicks();
        const id = e.target.closest("[data-outcome-row]").querySelector('input[name="outcome_id"]').value;
        const label = probMcRows.querySelector(`label[for="p-${CSS.escape(id)}"]`);
        if (label) label.textContent = e.target.value.trim() || label.textContent;
      }
      const row = e.target.closest("[data-bettor-row]");
      if (row) {
        updatePayout(row);
        if (e.target.matches("[data-amount]")) matchOtherSide(row, "amount");
        else if (e.target.matches("[data-value]")) matchOtherSide(row, "value");
      }
    });

    form.addEventListener("click", (e) => {
      if (e.target.closest("[data-add-outcome]")) addOutcome();
      else if (e.target.closest("[data-add-bettor]")) addBettor();
      else if (e.target.closest("[data-resolve]")) resolveLink(e.target.closest("[data-resolve]"));
      else if (e.target.closest("[data-unlink]")) { clearLink(); pmUrl.focus(); }
      else if (e.target.closest("[data-remove-outcome]")) {
        const row = e.target.closest("[data-outcome-row]");
        const next = row.nextElementSibling || row.previousElementSibling;
        row.remove();
        syncOutcomeButtons();
        rebuildPicks();
        rebuildMcProbabilities();
        if (next) next.querySelector('input[name="outcome_label"]').focus();
      } else if (e.target.closest("[data-remove-bettor]")) {
        const row = e.target.closest("[data-bettor-row]");
        const next = row.nextElementSibling || row.previousElementSibling;
        row.remove();
        syncBettorButtons();
        if (next) next.querySelector('input[name="pos_name"]').focus();
      }
    });

    pmUrl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        resolveLink(form.querySelector("[data-resolve]"));
      }
    });

    if (linked()) {
      try {
        const link = JSON.parse(pmJson.value);
        pmSelected.querySelector("[data-pm-prices]").textContent = "";
        pmUrl.value = link.url;
      } catch (_) { clearLink(); }
    }
    syncOutcomeButtons();
    syncBettorButtons();
    syncProbabilityMode();
    form.updateProbabilities();
    bettorRows.querySelectorAll("[data-bettor-row]").forEach(updatePayout);
  }

  // --- start -------------------------------------------------------------

  applyTab();
  refresh();
  setInterval(refreshIfDue, 5000);
  document.addEventListener("visibilitychange", refreshIfDue);
})();
