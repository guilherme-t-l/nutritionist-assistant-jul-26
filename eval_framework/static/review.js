/**
 * Review screen renderer — chat + meal plan the way the product UI shows them.
 *
 * Reads JSON from #review-data (injected by the Jinja template). Assistant
 * turns that are MealPlan JSON become short chat notes + a rendered plan
 * panel (meals, macros, calorie and macro deltas). Raw JSON stays behind a toggle.
 */
(function () {
  const dataEl = document.getElementById("review-data");
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const transcript = data.transcript || [];
  const context = data.context || {};
  const conversationId = data.conversation_id;
  const nextUrl = data.next_url;
  const calorieTarget = Number(context.calorie_target) || null;

  const noteEl = document.getElementById("note");
  const flash = document.getElementById("status-flash");
  const chatThread = document.getElementById("chat-thread");
  const planCard = document.getElementById("plan-card");
  const planVersions = document.getElementById("plan-versions");
  const planTitle = document.getElementById("plan-toolbar-title");
  const rawEl = document.getElementById("raw-json");

  let busy = false;
  let activePlanIndex = 0;

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (ch) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[ch]
    );
  }

  // Blank macro targets are omitted. 0 is a real target, so don't treat it as missing.
  function optionalTarget(value) {
    if (value == null || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  // Same 10% band as the product plan panel.
  function renderTargetRow(actual, target, unitLabel) {
    if (target == null) return "";
    const goal = Number(target);
    if (!Number.isFinite(goal)) return "";
    const delta = actual - goal;
    const withinTen = Math.abs(delta) <= Math.abs(goal) * 0.1;
    const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
    return `
      <div class="target-row">
        <span>Target: ${goal} ${unitLabel}</span>
        <span class="delta ${withinTen ? "ok" : "off"}">Δ ${sign}${Math.abs(delta)} ${unitLabel}</span>
      </div>`;
  }

  function tryParsePlan(content) {
    if (typeof content !== "string") return null;
    try {
      const parsed = JSON.parse(content);
      if (parsed && Array.isArray(parsed.meals)) return parsed;
      if (parsed && parsed.error) return { __error: parsed.error };
      return null;
    } catch (_) {
      return null;
    }
  }

  function mealMacros(meal) {
    const foods = meal.ingredients || [];
    const sum = (key) =>
      foods.reduce((acc, f) => acc + (Number(f[key]) || 0), 0);
    return {
      calories: meal.calories != null ? meal.calories : sum("calories"),
      protein_g: meal.protein_g != null ? meal.protein_g : sum("protein_g"),
      carbs_g: meal.carbs_g != null ? meal.carbs_g : sum("carbs_g"),
      fat_g: meal.fat_g != null ? meal.fat_g : sum("fat_g"),
    };
  }

  function renderMeal(meal) {
    // Heading is meal.name only — no position-based Breakfast/Lunch overlay.
    const macros = mealMacros(meal);
    const foods = (meal.ingredients || [])
      .map((food) => {
        const p = food.protein_g ?? 0;
        const c = food.carbs_g ?? 0;
        const f = food.fat_g ?? 0;
        return `
        <li class="food-row">
          <span class="food-main">
            <span class="food-name-line">
              <span class="food-name">${escapeHtml(food.name || "")}</span>
              <span class="food-qty">${escapeHtml(food.quantity || "")}</span>
            </span>
            <span class="food-macros">P ${p}g · C ${c}g · F ${f}g</span>
          </span>
          <span class="food-meta">${food.calories ?? 0} kcal</span>
        </li>`;
      })
      .join("");
    const desc = meal.description
      ? `<p class="meal-desc">${escapeHtml(meal.description)}</p>`
      : "";
    return `
      <section class="meal-block">
        <div class="meal-head">
          <h3 class="meal-name">${escapeHtml(meal.name || "")}</h3>
          <span class="meal-accent" aria-hidden="true"></span>
        </div>
        ${desc}
        <ul class="food-list">${foods}</ul>
        <div class="meal-total">
          <span>Meal total</span>
          <span>
            <strong>${macros.calories}</strong> kcal &nbsp;|&nbsp;
            P ${macros.protein_g}g &nbsp; C ${macros.carbs_g}g &nbsp; F ${macros.fat_g}g
          </span>
        </div>
      </section>`;
  }

  function renderPlan(plan) {
    if (!plan || plan.__error) {
      const detail =
        plan && plan.__error
          ? escapeHtml(
              typeof plan.__error === "string"
                ? plan.__error
                : JSON.stringify(plan.__error)
            )
          : "No meal plan in this turn.";
      planCard.innerHTML = `
        <div class="plan-empty">
          <p class="plan-empty-title">No plan to show</p>
          <p class="plan-empty-copy">${detail}</p>
        </div>`;
      return;
    }
    if (!plan.meals || plan.meals.length === 0) {
      planCard.innerHTML = `
        <div class="plan-empty">
          <p class="plan-empty-title">Your meal plan</p>
          <p class="plan-empty-copy">Empty meals list.</p>
        </div>`;
      return;
    }

    let html = "";
    plan.meals.forEach((meal) => {
      html += renderMeal(meal);
    });

    const totals = plan.meals.reduce(
      (acc, m) => {
        const mm = mealMacros(m);
        acc.kcal += mm.calories;
        acc.p += mm.protein_g;
        acc.c += mm.carbs_g;
        acc.f += mm.fat_g;
        return acc;
      },
      { kcal: 0, p: 0, c: 0, f: 0 }
    );

    html += `
      <div class="day-total">
        <span class="label">Daily total</span>
        <span class="macros">
          <strong>${totals.kcal}</strong> kcal &nbsp;|&nbsp;
          P ${totals.p}g &nbsp; C ${totals.c}g &nbsp; F ${totals.f}g
        </span>
      </div>`;

    html += renderTargetRow(totals.kcal, calorieTarget, "kcal");
    html += renderTargetRow(totals.p, optionalTarget(context.protein_g_target), "g protein");
    html += renderTargetRow(totals.c, optionalTarget(context.carbs_g_target), "g carbs");
    html += renderTargetRow(totals.f, optionalTarget(context.fat_g_target), "g fat");

    if (plan.notes) {
      html += `<p class="plan-notes">${escapeHtml(plan.notes)}</p>`;
    }
    planCard.innerHTML = html;
  }

  const plans = [];
  const chatItems = [];
  transcript.forEach((turn) => {
    const role = turn.role || "";
    const content = turn.content || "";
    if (role === "user") {
      chatItems.push({ role: "user", text: content, planIndex: null });
      return;
    }
    const parsed = tryParsePlan(content);
    if (parsed && parsed.__error) {
      chatItems.push({
        role: "error",
        text:
          "Agent error: " +
          (parsed.__error.detail || JSON.stringify(parsed.__error)),
        planIndex: null,
      });
      return;
    }
    if (parsed) {
      const planIndex = plans.length;
      plans.push(parsed);
      const note = (parsed.notes || "").trim();
      chatItems.push({
        role: "assistant",
        text: note || "Here's your updated meal plan.",
        planIndex,
      });
      return;
    }
    chatItems.push({ role: "assistant", text: content, planIndex: null });
  });

  if (plans.length) activePlanIndex = plans.length - 1;

  function paintChat() {
    chatThread.innerHTML = chatItems
      .map((item) => {
        if (item.role === "user") {
          return `<div class="bubble user">${escapeHtml(item.text)}</div>`;
        }
        if (item.role === "error") {
          return `<div class="bubble error">${escapeHtml(item.text)}</div>`;
        }
        const active = item.planIndex === activePlanIndex ? " active-plan" : "";
        const clickable = item.planIndex != null ? " clickable" : "";
        const attr =
          item.planIndex != null ? ` data-plan-index="${item.planIndex}"` : "";
        return `<div class="bubble assistant${active}${clickable}"${attr}>${escapeHtml(item.text)}</div>`;
      })
      .join("");

    chatThread.querySelectorAll("[data-plan-index]").forEach((el) => {
      el.addEventListener("click", () => {
        activePlanIndex = Number(el.dataset.planIndex);
        paint();
      });
    });
  }

  function paintVersions() {
    if (plans.length <= 1) {
      planVersions.innerHTML = "";
      planTitle.textContent = "Today's menu";
      return;
    }
    planTitle.textContent = "Plan version";
    planVersions.innerHTML = plans
      .map((_, i) => {
        const active = i === activePlanIndex ? " active" : "";
        return `<button type="button" class="plan-version${active}" data-plan-index="${i}">v${i + 1}</button>`;
      })
      .join("");
    planVersions.querySelectorAll("[data-plan-index]").forEach((el) => {
      el.addEventListener("click", () => {
        activePlanIndex = Number(el.dataset.planIndex);
        paint();
      });
    });
  }

  function paint() {
    paintChat();
    paintVersions();
    renderPlan(plans[activePlanIndex] || null);
  }

  rawEl.textContent = JSON.stringify(transcript, null, 2);
  document.getElementById("toggle-raw").addEventListener("click", () => {
    const open = rawEl.classList.toggle("open");
    document.getElementById("toggle-raw").textContent = open
      ? "Hide raw transcript"
      : "Show raw transcript";
  });

  paint();

  async function rate(verdict) {
    if (busy) return;
    busy = true;
    flash.textContent = "Saving…";
    try {
      const res = await fetch("/conversations/" + conversationId + "/verdict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rater_type: "human",
          verdict: verdict,
          note: noteEl.value.trim() || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      if (nextUrl) {
        window.location.href = nextUrl;
      } else {
        flash.textContent = "Saved. Batch complete.";
        busy = false;
      }
    } catch (err) {
      flash.textContent = "Error: " + err.message;
      busy = false;
    }
  }

  document.getElementById("btn-pass").addEventListener("click", () => rate("pass"));
  document.getElementById("btn-fail").addEventListener("click", () => rate("fail"));

  document.addEventListener("keydown", (e) => {
    if (
      e.target === noteEl ||
      e.target.tagName === "TEXTAREA" ||
      e.target.tagName === "INPUT"
    ) {
      return;
    }
    if (e.key === "p" || e.key === "P") {
      e.preventDefault();
      rate("pass");
    } else if (e.key === "f" || e.key === "F") {
      e.preventDefault();
      rate("fail");
    }
  });

  const toggle = document.getElementById("toggle-judge");
  const panel = document.getElementById("judge-panel");
  if (toggle && panel) {
    toggle.addEventListener("click", () => {
      const hidden = panel.classList.toggle("hidden");
      toggle.textContent = hidden ? "Show judge verdict" : "Hide judge verdict";
    });
  }
})();
