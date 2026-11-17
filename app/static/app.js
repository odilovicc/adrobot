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
        $input.trigger("focus").trigger("select");
        return;
      }
      default: return;
    }
    $card.find("button").prop("disabled", true);
    req.done((html) => {
      swapCard($card, html);
      if (action === "push") toast("Опубликовано в Keitaro", "success");
    }).fail(() => $card.find("button").prop("disabled", false));
  });

  function commitShare($input) {
    if ($input.data("done")) return;
    $input.data("done", true);
    const $card = $input.closest(".stream");
    const linkId = $input.closest("tr").data("link");
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

  function reloadCard($card) {
    const campaign = $("#streams").data("campaign");
    $.get(`/api/campaigns/${campaign}/streams`).done((html) => {
      const $fresh = $(html).filter(`#${$card.attr("id")}`);
      if ($fresh.length) swapCard($card, $fresh[0].outerHTML);
    });
  }

  $(document).on("keydown", ".share-input", function (e) {
    if (e.key === "Enter") { e.preventDefault(); commitShare($(this)); }
    if (e.key === "Escape") { $(this).data("done", true); reloadCard($(this).closest(".stream")); }
  });
  $(document).on("blur", ".share-input", function () { commitShare($(this)); });

  // --- Fetch ---------------------------------------------------------------
  function doFetch() {
    const $btn = $("#fetch-btn");
    busy($btn, true);
    post($btn.data("url"))
      .done((html) => {
        destroyOfferSearch($("#streams"));
        $("#streams").html(html);
        initOfferSearch($("#streams"));
        applyRemovedFilter();
        toast("Потоки загружены из Keitaro", "success");
      })
      .always(() => busy($btn, false));
  }

  $(document).on("click", "#fetch-btn", function () {
    if ($('.stream[data-dirty="true"]').length) {
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

  // --- Creator form ----------------------------------------------------------
  $(document).on("submit", "#creator", function () {
    busy($(this).find("button[type=submit]"), true);
  });

  $(function () { initOfferSearch($(document)); });
})(jQuery);
