/* Adrobot front-end: jQuery + Select2. Server returns re-rendered stream cards. */
(function ($) {
  "use strict";

  function toast(message, kind) {
    const $t = $(
      '<div class="toast align-items-center border-0" role="alert" aria-live="assertive" aria-atomic="true">' +
        '<div class="d-flex"><div class="toast-body"></div>' +
        '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Закрыть"></button></div></div>'
    );
    $t.addClass(kind === "success" ? "text-bg-success" : "text-bg-danger");
    $t.find(".toast-body").text(message);
    $("#toasts").append($t);
    const t = new bootstrap.Toast($t[0], { delay: kind === "success" ? 2500 : 8000 });
    $t.on("hidden.bs.toast", () => $t.remove());
    t.show();
  }

  function errorText(xhr) {
    if (xhr.responseJSON && xhr.responseJSON.error) return xhr.responseJSON.error;
    if (xhr.status === 0) return "Сервер недоступен";
    return "Ошибка " + xhr.status + ": " + (xhr.statusText || "unknown");
  }

  function post(url, data) {
    return $.ajax({
      url: url,
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(data || {}),
      dataType: "html",
      converters: { "text html": true },
    }).fail((xhr) => {
      try { xhr.responseJSON = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
      toast(errorText(xhr));
    });
  }

  function initOfferSearch($root) {
    $root.find("select[data-offer-search]").each(function () {
      const $s = $(this);
      if ($s.data("select2")) return;
      $s.select2({
        theme: "bootstrap-5",
        width: "100%",
        placeholder: "Поиск оффера по ID или имени",
        allowClear: true,
        minimumInputLength: 0,
        ajax: {
          url: "/api/offers",
          delay: 250,
          data: (params) => {
            const streamId = $s.closest(".stream").data("id");
            return streamId ? { q: params.term || "", stream_id: streamId } : { q: params.term || "" };
          },
          processResults: (data) => data,
          error: (xhr) => { if (xhr.statusText !== "abort") toast("Не удалось загрузить офферы"); },
        },
      });
      // Select2 + jQuery ≥ 3.6 doesn't focus the search box on open.
      $s.on("select2:open", () => {
        const search = document.querySelector(".select2-container--open .select2-search__field");
        if (search) search.focus();
      });
    });
  }

  function destroyOfferSearch($root) {
    $root.find("select[data-offer-search]").each(function () {
      if ($(this).data("select2")) $(this).select2("destroy");
    });
  }

  function applyRemovedFilter() {
    const hide = $("#toggle-removed").attr("aria-pressed") === "false";
    $("tr.offer-removed").toggleClass("d-none", hide);
  }

  function swapCard($card, html) {
    const $new = $(html);
    destroyOfferSearch($card);
    $card.replaceWith($new);
    initOfferSearch($new);
    applyRemovedFilter();
    updateDirtyBar();
    return $new;
  }

  function busy($btn, on) {
    $btn.prop("disabled", on).find(".spinner-border").toggleClass("d-none", !on);
  }

  // --- Stream editor -----------------------------------------------------
  $(document).on("click", ".stream [data-action]", function (e) {
    const $btn = $(this);
    const action = $btn.data("action");
    const $card = $btn.closest(".stream");
    const streamId = $card.data("id");
    const linkId = $btn.closest("tr").data("link");
    let req;

    switch (action) {
      case "add": {
        const offerId = $card.find("select[data-offer-search]").val();
        if (!offerId) { toast("Выберите оффер"); return; }
        req = post(`/api/streams/${streamId}/offers`, { offer_id: Number(offerId) });
        break;
      }
      case "push": req = post(`/api/streams/${streamId}/push`); break;
      case "cancel": req = post(`/api/streams/${streamId}/cancel`); break;
      case "remove": req = post(`/api/stream-offers/${linkId}/remove`); break;
      case "restore": req = post(`/api/stream-offers/${linkId}/restore`); break;
      case "pin":
        req = post(`/api/stream-offers/${linkId}/pin`, { pinned: $btn.data("pinned") !== true });
        break;
      case "edit-share": {
        e.preventDefault();
        const $input = $('<input type="number" min="0" max="100" step="1" class="form-control form-control-sm share-input" aria-label="Вес, %">')
          .val($btn.data("share"));
        $btn.replaceWith($input);
        bindShareInput($input);
        $input.trigger("focus").trigger("select");
        return;
      }
      default: return;
    }
    $card.find("button").prop("disabled", true);
    if (action === "push") busy($btn, true);
    req.done((html) => {
      swapCard($card, html);
      if (action === "push") toast("Опубликовано в Keitaro", "success");
    }).fail(() => { $card.find("button").prop("disabled", false); busy($btn, false); });
  });

  // The input lives until the card is re-rendered. replaceWith() wipes jQuery data and then
  // fires blur on the detached input, so the "already handled" flag is a closure variable.
  function bindShareInput($input) {
    let done = false;
    const $card = $input.closest(".stream");
    const linkId = $input.closest("tr").data("link");

    function commit() {
      if (done) return;
      done = true;
      const raw = String($input.val()).trim();
      const value = Number(raw);
      if (raw === "" || !Number.isInteger(value) || value < 0 || value > 100) {
        toast("Вес должен быть целым числом от 0 до 100");
        reloadCard($card);
        return;
      }
      post(`/api/stream-offers/${linkId}/share`, { share: value })
        .done((html) => swapCard($card, html))
        .fail(() => reloadCard($card));
    }

    $input.on("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      if (e.key === "Escape") { done = true; reloadCard($card); }
    });
    $input.on("blur", commit);
  }

  function reloadCard($card) {
    const campaign = $("#streams").data("campaign");
    $.get(`/api/campaigns/${campaign}/streams`).done((html) => {
      const $fresh = $(html).filter(`#${$card.attr("id")}`);
      if ($fresh.length) swapCard($card, $fresh[0].outerHTML);
    });
  }

  function localizeTimes($root) {
    $root.find("time[data-local]").each(function () {
      const d = new Date(this.getAttribute("datetime"));
      if (isNaN(d)) return;
      this.title = d.toLocaleString();
      this.textContent = this.dataset.local === "date"
        ? d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
        : d.toLocaleString();
    });
  }

  // --- Fetch ---------------------------------------------------------------
  function doFetch() {
    const $btn = $("#fetch-btn");
    busy($btn, true);
    post($btn.data("url"))
      .done((html) => {
        destroyOfferSearch($("#streams"));
        $("#streams").html(html);
        initOfferSearch($("#streams"));
        localizeTimes($("#streams"));
        applyRemovedFilter();
        updateDirtyBar();
        toast("Потоки загружены из Keitaro", "success");
      })
      .always(() => busy($btn, false));
  }

  $(document).on("click", "#fetch-btn", function () {
    const $dirty = $('.stream[data-dirty="true"]');
    if ($dirty.length) {
      $("#fetch-confirm-list").empty().append($dirty.map((_, el) => $("<li>").text($(el).data("name"))[0]).get());
      bootstrap.Modal.getOrCreateInstance("#fetch-confirm").show();
    } else {
      doFetch();
    }
  });
  $(document).on("click", "#fetch-confirm-ok", function () {
    bootstrap.Modal.getInstance("#fetch-confirm").hide();
    doFetch();
  });

  $(document).on("click", "#toggle-removed", function () {
    const $b = $(this);
    const showing = $b.attr("aria-pressed") === "true";
    $b.attr("aria-pressed", showing ? "false" : "true");
    $b.find("span").text(showing ? "Show all offers" : "Hide removed");
    applyRemovedFilter();
  });


  // --- Editor helpers ----------------------------------------------------------
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }

  function updateDirtyBar() {
    const n = $('.stream[data-dirty="true"]').length;
    $("#dirty-bar").toggleClass("d-none", n === 0);
    $("#dirty-count").text(n);
    $("#dirty-label").text(plural(n, "поток", "потока", "потоков"));
  }

  $(document).on("click", "[data-copy]", function () {
    const $b = $(this), text = String($b.data("copy"));
    const done = () => toast("Скопировано: " + text, "success");
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => toast("Не удалось скопировать"));
  });

  // --- Theme -------------------------------------------------------------------
  $(document).on("click", "#theme-toggle", function () {
    const next = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-bs-theme", next);
    try { localStorage.setItem("adrobot-theme", next); } catch (e) { /* private mode */ }
  });

  // --- Campaign list: live filters --------------------------------------------
  const $filters = $("#campaign-filters");
  let listXhr = null;
  let searchTimer = null;

  function listUrlFromForm() {
    const params = new URLSearchParams();
    $filters.serializeArray().forEach(({ name, value }) => {
      const isDefault = (name === "sort" && value === "id") || (name === "per" && value === "25") ||
        (name === "dir" && value === "desc" && ["id", "created"].includes($filters.find("[name=sort]").val()));
      if (value !== "" && !isDefault) params.set(name, value);
    });
    const qs = params.toString();
    return "/" + (qs ? "?" + qs : "");
  }

  function syncFormFromUrl(url) {
    const p = new URL(url, location.origin).searchParams;
    $("#f-q").val(p.get("q") || "");
    $filters.find(`[name=state][value="${p.get("state") || ""}"]`).prop("checked", true);
    $("#f-mine").prop("checked", p.get("mine") === "1");
    $filters.find("[name=sort]").val(p.get("sort") || "id");
    $filters.find("[name=dir]").val(p.get("dir") || (["name", "group", "state"].includes(p.get("sort")) ? "asc" : "desc"));
    $filters.find("[name=per]").val(p.get("per") || "25");
  }

  function loadList(url, push) {
    if (listXhr) listXhr.abort();
    $("#campaign-table .table-card").addClass("is-loading");
    listXhr = $.ajax({ url: url, headers: { "X-Requested-With": "XMLHttpRequest" }, dataType: "html" })
      .done((html) => {
        $("#campaign-table").html(html);
        const facets = document.getElementById("facets");
        if (facets) {
          $(facets.content).find("[data-count]").each(function () {
            $filters.find(`[data-count="${this.dataset.count}"]`).text(this.textContent);
          });
          const group = $("#f-group").val();
          $("#f-group").html($(facets.content).find("select").html()).val(group);
          $("#f-reset").toggleClass("d-none", facets.dataset.reset !== "show");
          $("#f-refresh").attr("href", (facets.dataset.url === "?" ? "?" : facets.dataset.url + "&") + "refresh=1");
          url = "/" + (facets.dataset.url === "?" ? "" : facets.dataset.url);
        }
        localizeTimes($("#campaign-table"));
        if (push === "push") history.pushState({ list: true }, "", url);
        else if (push === "replace") history.replaceState({ list: true }, "", url);
      })
      .fail((xhr) => { if (xhr.statusText !== "abort") toast("Не удалось обновить список"); })
      .always(() => $("#campaign-table .table-card").removeClass("is-loading"));
  }

  if ($filters.length) {
    // Table header sticks right under the sticky filter bar.
    const setFiltersH = () => document.documentElement.style.setProperty("--filters-h", $filters.outerHeight() + "px");
    setFiltersH();
    $(window).on("resize", setFiltersH);
    $filters.on("submit", (e) => { e.preventDefault(); loadList(listUrlFromForm(), "push"); });
    $("#f-q").on("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadList(listUrlFromForm(), "replace"), 300);
    });
    $filters.on("change", "[name=state], #f-group, #f-mine", () => loadList(listUrlFromForm(), "push"));
    $(document).on("click", "#campaign-table a[data-nav], #f-reset", function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      const href = this.getAttribute("href");
      const url = href.startsWith("?") ? "/" + href : href;
      syncFormFromUrl(url);
      loadList(url, "push");
      if (this.closest(".pagination")) document.getElementById("main").scrollIntoView({ block: "start" });
    });
    $(document).on("change", "#f-per", function () {
      const url = "/" + this.value;
      syncFormFromUrl(url);
      loadList(url, "push");
    });
    window.addEventListener("popstate", () => { syncFormFromUrl(location.href); loadList(location.pathname + location.search); });
    // Whole row opens the campaign; inner links/buttons keep their own behaviour.
    $(document).on("click", "tr.row-link", function (e) {
      if ($(e.target).closest("a, button, input, select").length) return;
      if (window.getSelection && String(window.getSelection())) return;
      location.href = this.dataset.href;
    });
    $(document).on("keydown", (e) => {
      if (e.key === "/" && !$(e.target).is("input, textarea, select, [contenteditable]")) {
        e.preventDefault();
        $("#f-q").trigger("focus").trigger("select");
      }
      if (e.key === "Escape" && e.target.id === "f-q" && e.target.value) {
        e.target.value = "";
        loadList(listUrlFromForm(), "replace");
      }
    });
  }

  // --- Creator: searchable selects, geo chips, live preview -----------------------
  function initCreator() {
    const $form = $("#creator");
    if (!$form.length) return;
    $form.find("select[data-searchable]").each(function () {
      const $s = $(this);
      $s.select2({ theme: "bootstrap-5", width: "100%", placeholder: $s.data("placeholder"), allowClear: false });
      $s.on("select2:open", () => {
        const search = document.querySelector(".select2-container--open .select2-search__field");
        if (search) search.focus();
      });
    });

    function selectedText($s, fallback) {
      const t = $s.find("option:selected").text().trim();
      return t ? t.replace(/\s·\s#\d+$/, "") : fallback;
    }

    function render() {
      $("#pv-name").text($("#name").val().trim() || "Без названия");
      $("#pv-domain").text(selectedText($("#domain_id"), "домен не выбран"));
      $("#pv-group").text(selectedText($("#group_id"), "без группы"));
      const codes = $("#geo").val().toUpperCase().split(/[\s,;]+/).filter(Boolean);
      const unique = [...new Set(codes)];
      const chips = unique.map((c) => $("<span>").addClass("chip mono").toggleClass("chip-bad", !/^[A-Z]{2}$/.test(c)).text(c));
      $("#geo-chips").empty().append(chips.map(($c) => $c.clone()));
      $("#pv-geo").empty().append(chips.length ? chips : $('<span class="text-secondary">—</span>'));
      const offer = $("#offer_id").find("option:selected").text().trim();
      $("#pv-offer").text(offer || "оффер не выбран");
    }

    $form.on("input change", "input, select", render);
    $("#offer_id").on("select2:select select2:clear", render);
    render();

    const $summary = $("#error-summary");
    if ($summary.length) {
      const $first = $form.find(".is-invalid").first();
      if ($first.is("select") && $first.data("select2")) $first.select2("open");
      else $first.trigger("focus");
    }
    $form.on("submit", function () {
      const $btn = $(this).find("button[type=submit]");
      busy($btn, true);
      $btn.find(".label").text("Создаём в Keitaro…");
    });
  }

  $(function () {
    initOfferSearch($(document));
    initCreator();
    localizeTimes($(document));
    updateDirtyBar();
  });
})(jQuery);
