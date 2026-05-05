'use strict';

const PASSIVE_SCANNER_MAX_KEY_INTERVAL_MS = 45;
const PASSIVE_SCANNER_IDLE_COMMIT_MS = 90;
const PASSIVE_SCANNER_MIN_DIGITS = 9;

let passiveScannerBuffer = '';
let passiveScannerLastKeyAt = 0;
let passiveScannerTimer = null;
let passiveScannerSourceElement = null;
let passiveScannerSourceInitialValue = '';

function resetPassiveScannerBuffer() {
  passiveScannerBuffer = '';
  passiveScannerLastKeyAt = 0;
  clearTimeout(passiveScannerTimer);
  passiveScannerTimer = null;
  passiveScannerSourceElement = null;
  passiveScannerSourceInitialValue = '';
}

function passiveScannerDigitsCount(value) {
  return Array.from(String(value || '')).filter((char) => /\d/.test(char)).length;
}

function passiveScannerCanHandle() {
  if (!currentCashier) return false;
  if (document.querySelector('#qrModal.show')) return false;
  const active = document.activeElement;
  if (!active) return true;
  return !active.closest('#cashierAuthModal');
}

function passiveScannerShouldRestoreField(element) {
  if (!element || !('value' in element)) return false;
  return element !== document.getElementById('phoneInput') && element !== document.getElementById('qrInput');
}

function routePassiveScannerInput(rawValue, sourceElement = document.activeElement) {
  const normalizedValue = normalizePhoneLikeScannerInput(rawValue);
  if (passiveScannerDigitsCount(normalizedValue) < PASSIVE_SCANNER_MIN_DIGITS) return false;

  const restoreElement = passiveScannerSourceElement || sourceElement;
  if (passiveScannerShouldRestoreField(restoreElement)) {
    try {
      restoreElement.value = passiveScannerSourceInitialValue;
      restoreElement.dispatchEvent(new Event('input', { bubbles: true }));
    } catch {}
  }

  const phoneInput = document.getElementById('phoneInput');
  if (phoneInput) {
    phoneInput.value = normalizedValue;
    phoneInput.focus();
    phoneInput.select?.();
  }

  const source = normalizedValue.startsWith('+') ? 'phone' : 'qr';
  findClient(normalizedValue, source);
  return true;
}

function commitPassiveScannerBuffer(sourceElement = document.activeElement) {
  const rawValue = passiveScannerBuffer;
  const restoreElement = passiveScannerSourceElement || sourceElement;
  const restoreValue = passiveScannerSourceInitialValue;
  resetPassiveScannerBuffer();
  if (!rawValue) return false;
  passiveScannerSourceElement = restoreElement;
  passiveScannerSourceInitialValue = restoreValue;
  const handled = routePassiveScannerInput(rawValue, restoreElement);
  passiveScannerSourceElement = null;
  passiveScannerSourceInitialValue = '';
  return handled;
}

function schedulePassiveScannerCommit(sourceElement = document.activeElement) {
  clearTimeout(passiveScannerTimer);
  passiveScannerTimer = setTimeout(() => {
    commitPassiveScannerBuffer(sourceElement);
  }, PASSIVE_SCANNER_IDLE_COMMIT_MS);
}

async function loadCashierShiftStatus(silent = false) {
  if (!cashierToken || !currentCashier) {
    cashierShiftState = { has_active_shift: false, shift: null, summary: null };
    syncSessionOpsStorage(true);
    setCashierShiftUI();
    updateProceedBtn();
    return;
  }
  try {
    const r = await apiGetCashier('/api/cashiers/shift', { cacheKey: 'cashier_shift' });
    if (!r.ok) {
      cashierShiftState = { has_active_shift: false, shift: null, summary: null };
      syncSessionOpsStorage(true);
      setCashierShiftUI();
      updateProceedBtn();
      if (!silent) showToast(tr('cashier_shift_status_error', 'Не удалось загрузить состояние смены'), 'warning');
      return;
    }
    const data = await r.json().catch(() => ({}));
    cashierShiftState = data && typeof data === 'object'
      ? { has_active_shift: !!data.has_active_shift, shift: data.shift || null, summary: data.summary || null }
      : { has_active_shift: false, shift: null, summary: null };
    syncSessionOpsStorage();
    setCashierShiftUI();
    updateProceedBtn();
    if (!silent && window.AZSOffline?.isCachedResponse(r)) {
      showToast('Связь слабая. Показано последнее сохраненное состояние смены', 'warning');
    }
  } catch {
    cashierShiftState = { has_active_shift: false, shift: null, summary: null };
    syncSessionOpsStorage(true);
    setCashierShiftUI();
    updateProceedBtn();
    if (!silent) showToast(tr('cashier_shift_status_error', 'Не удалось загрузить состояние смены'), 'warning');
  }
}

function prefillCancelReceipt(checkId) {
  const input = document.getElementById('cancelCheckId');
  if (!input) return;
  input.value = checkId || '';
  input.focus();
}

function markCancelledReceipt(checkId) {
  sessionOps = sessionOps.map(op => {
    if (op.checkId !== checkId) return op;
    return {...op, cancelled: true, canCancel: false};
  });
  persistSessionOps();
  renderOps();
}

async function cancelReceiptById(checkId = null) {
  if (!currentCashier) {
    showToast(tr('cashier_signin_required'), 'warning');
    return;
  }
  if (!ensureCashierShiftActive()) return;

  const value = (checkId ?? document.getElementById('cancelCheckId')?.value ?? '').trim();
  const reason = (document.getElementById('cancelReason')?.value ?? '').trim();
  if (!value) {
    showToast(tr('cashier_cancel_receipt_fill', 'Укажите номер чека'), 'warning');
    return;
  }
  if (!window.confirm(tr('cashier_cancel_receipt_confirm', 'Отменить этот чек?'))) {
    return;
  }

  const btn = document.getElementById('btnCancelReceipt');
  const initialHtml = btn?.innerHTML;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${tr('button_cancel_receipt', 'Отменить чек')}`;
  }
  try {
    const r = await apiPostCashierQueued('/api/cash/cancel-receipt', {check_id: value, reason: reason || null}, {
      invalidateCacheKeys: ['cashier_shift'],
      replayLabel: 'cashier-cancel-receipt',
    });
    if (window.AZSOffline?.isQueuedResponse(r)) {
      if (document.getElementById('cancelCheckId')) document.getElementById('cancelCheckId').value = '';
      if (document.getElementById('cancelReason')) document.getElementById('cancelReason').value = '';
      showToast('Отмена чека поставлена в очередь и отправится после восстановления связи', 'warning');
      return;
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      showToast(data.detail || tr('cashier_cancel_receipt_error', 'Не удалось отменить чек'), r.status === 409 ? 'warning' : 'danger');
      return;
    }
    markCancelledReceipt(value);
    if (document.getElementById('cancelCheckId')) document.getElementById('cancelCheckId').value = '';
    if (document.getElementById('cancelReason')) document.getElementById('cancelReason').value = '';
    await loadCashierShiftStatus(true);
    if (currentClient && data.client_phone && currentClient.phone === data.client_phone) {
      try {
        const r2 = await apiPost('/api/cash/client-info', {phone: currentClient.phone});
        if (r2.ok) {
          currentClient = await r2.json();
          document.getElementById('clientBalance').textContent =
            parseFloat(currentClient.bonus_balance || 0).toLocaleString(appI18n.getCurrentLocale(), {maximumFractionDigits:2});
        }
      } catch {}
    }
    showToast(tr('cashier_cancel_receipt_success', 'Чек отменён'), 'success');
  } catch {
    showToast(tr('cashier_connection_error'), 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = initialHtml;
    }
  }
}

function ensureCashierShiftActive() {
  if (hasActiveCashierShift()) return true;
  showToast(tr('cashier_shift_required', 'Сначала откройте смену'), 'warning');
  return false;
}

async function openCashierShift() {
  if (!currentCashier) {
    showToast(tr('cashier_signin_required'), 'warning');
    return;
  }
  const btn = document.getElementById('btnShiftOpen');
  const initialHtml = btn?.innerHTML;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${tr('button_open_shift')}`;
  }
  try {
    const r = await apiPostCashierQueued('/api/cashiers/shift/open', {}, {
      invalidateCacheKeys: ['cashier_shift'],
      replayLabel: 'cashier-shift-open',
    });
    if (window.AZSOffline?.isQueuedResponse(r)) {
      showToast('Открытие смены поставлено в очередь. Смена откроется после восстановления связи', 'warning');
      return;
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (r.status === 409) {
        await loadCashierShiftStatus(true);
      }
      showToast(
        data.detail || tr('cashier_shift_open_error', 'Не удалось открыть смену'),
        r.status === 409 ? 'warning' : 'danger'
      );
      return;
    }
    cashierShiftState = { has_active_shift: true, shift: data.shift || null, summary: data.summary || null };
    setCashierShiftUI();
    updateProceedBtn();
    showToast(tr('cashier_shift_opened', 'Смена открыта'), 'success');
  } catch {
    showToast(tr('cashier_connection_error'), 'danger');
  } finally {
    if (btn) btn.innerHTML = initialHtml;
    setCashierShiftUI();
  }
}

async function closeCashierShift() {
  if (!currentCashier) {
    showToast(tr('cashier_signin_required'), 'warning');
    return;
  }
  if (!hasActiveCashierShift()) {
    showToast(tr('cashier_shift_required', 'Сначала откройте смену'), 'warning');
    return;
  }
  if (!window.confirm(tr('cashier_shift_close_confirm', 'Закрыть текущую смену?'))) {
    return;
  }

  const btn = document.getElementById('btnShiftClose');
  const initialHtml = btn?.innerHTML;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${tr('button_close_shift')}`;
  }
  try {
    const r = await apiPostCashierQueued('/api/cashiers/shift/close', {}, {
      invalidateCacheKeys: ['cashier_shift'],
      replayLabel: 'cashier-shift-close',
    });
    if (window.AZSOffline?.isQueuedResponse(r)) {
      showToast('Закрытие смены поставлено в очередь и будет отправлено после восстановления связи', 'warning');
      return;
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (r.status === 409) {
        await loadCashierShiftStatus(true);
      }
      showToast(
        data.detail || tr('cashier_shift_close_error', 'Не удалось закрыть смену'),
        r.status === 409 ? 'warning' : 'danger'
      );
      return;
    }
    cashierShiftState = { has_active_shift: false, shift: data.shift || null, summary: data.summary || null };
    setCashierShiftUI();
    updateProceedBtn();

    const summary = data.summary || {};
    const parts = [];
    if (Number(summary.transactions_count || 0) > 0) parts.push(`${summary.transactions_count} ${tr('cashier_shift_transactions', 'операций')}`);
    if (Number(summary.fuel_sales_total || 0) > 0) parts.push(`${tr('cashier_shift_fuel_amount', 'Продажи топлива')}: ${money(summary.fuel_sales_total)}`);
    if (Number(summary.accrued_bonus_total || 0) > 0) parts.push(`${tr('cashier_shift_bonus_accrued', 'Начислено')}: ${num(summary.accrued_bonus_total)}`);
    if (Number(summary.redeemed_bonus_total || 0) > 0) parts.push(`${tr('cashier_shift_bonus_redeemed', 'Списано')}: ${num(summary.redeemed_bonus_total)}`);
    showToast(parts.length ? `${tr('cashier_shift_closed_done', 'Смена закрыта')}: ${parts.join(' · ')}` : tr('cashier_shift_closed_done', 'Смена закрыта'), 'info');
  } catch {
    showToast(tr('cashier_connection_error'), 'danger');
  } finally {
    if (btn) btn.innerHTML = initialHtml;
    setCashierShiftUI();
  }
}

function renderClientSearchSuggestions() {
  const box = document.getElementById('clientSearchSuggestions');
  if (!box) return;
  if (!clientSearchSuggestions.length) {
    box.classList.add('d-none');
    box.innerHTML = '';
    return;
  }

  box.innerHTML = clientSearchSuggestions.map((client, index) => {
    const avatarStyle = client.photo_data_url
      ? `background-image:url('${client.photo_data_url.replace(/'/g, '%27')}');background-color:#dbe7ff;color:transparent;`
      : '';
    return `
      <button type="button" class="client-search-suggestion ${index === clientSearchSuggestionIndex ? 'active' : ''}" data-index="${index}">
        <div class="client-search-suggestion-avatar" style="${avatarStyle}">${getClientInitials(client)}</div>
        <div class="client-search-suggestion-meta">
          <div class="fw-semibold">${client.name || client.phone}</div>
          <div class="text-muted small">${client.phone}</div>
        </div>
        <div class="text-end small text-muted">${num(client.bonus_balance || 0)}</div>
      </button>`;
  }).join('');
  box.classList.remove('d-none');
}

function hideClientSearchSuggestions() {
  clientSearchSuggestions = [];
  clientSearchSuggestionIndex = -1;
  renderClientSearchSuggestions();
}

function applyClientSuggestion(client) {
  if (!client) return;
  document.getElementById('phoneInput').value = client.phone || '';
  hideClientSearchSuggestions();
  setClientFound(client);
}

async function loadClientSearchSuggestions() {
  const input = document.getElementById('phoneInput');
  const query = input?.value?.trim() || '';
  const compact = query.replace(/[\s\-()]/g, '');
  if (!query || compact.length < 2) {
    hideClientSearchSuggestions();
    return;
  }

  const requestId = ++clientSearchSuggestRequestId;
  try {
    const response = await fetch(`/api/cash/clients?limit=5&offset=0&search=${encodeURIComponent(query)}`);
    if (!response.ok) {
      hideClientSearchSuggestions();
      return;
    }
    const results = await response.json();
    if (requestId !== clientSearchSuggestRequestId) return;
    clientSearchSuggestions = Array.isArray(results) ? results.slice(0, 5) : [];
    clientSearchSuggestionIndex = clientSearchSuggestions.length ? 0 : -1;
    renderClientSearchSuggestions();
  } catch {
    if (requestId === clientSearchSuggestRequestId) hideClientSearchSuggestions();
  }
}

function scheduleClientSearchSuggestions() {
  clearTimeout(clientSearchSuggestTimer);
  clientSearchSuggestTimer = setTimeout(loadClientSearchSuggestions, 120);
}

function handleClientSearchKeydown(event) {
  if (event.key === 'ArrowDown' && clientSearchSuggestions.length) {
    event.preventDefault();
    clientSearchSuggestionIndex = Math.min(clientSearchSuggestionIndex + 1, clientSearchSuggestions.length - 1);
    renderClientSearchSuggestions();
    return;
  }
  if (event.key === 'ArrowUp' && clientSearchSuggestions.length) {
    event.preventDefault();
    clientSearchSuggestionIndex = Math.max(clientSearchSuggestionIndex - 1, 0);
    renderClientSearchSuggestions();
    return;
  }
  if (event.key === 'Escape') {
    hideClientSearchSuggestions();
    return;
  }
  if (event.key === 'Enter') {
    event.preventDefault();
    if (clientSearchSuggestions.length && clientSearchSuggestionIndex >= 0) {
      applyClientSuggestion(clientSearchSuggestions[clientSearchSuggestionIndex]);
      return;
    }
    findClient();
  }
}

document.addEventListener('keydown', (event) => {
  if (!passiveScannerCanHandle()) {
    resetPassiveScannerBuffer();
    return;
  }
  if (event.ctrlKey || event.metaKey || event.altKey) {
    resetPassiveScannerBuffer();
    return;
  }

  const active = document.activeElement;
  const key = event.key || '';
  const now = typeof event.timeStamp === 'number' ? event.timeStamp : Date.now();

  if (key === 'Enter') {
    if (passiveScannerBuffer) {
      const handled = commitPassiveScannerBuffer(active);
      if (handled) {
        event.preventDefault();
        event.stopPropagation();
      }
    }
    return;
  }

  if (key.length !== 1) {
    if (now - passiveScannerLastKeyAt > PASSIVE_SCANNER_IDLE_COMMIT_MS) {
      resetPassiveScannerBuffer();
    }
    return;
  }

  if (passiveScannerLastKeyAt && (now - passiveScannerLastKeyAt) > PASSIVE_SCANNER_MAX_KEY_INTERVAL_MS) {
    resetPassiveScannerBuffer();
  }

  if (!passiveScannerBuffer && passiveScannerShouldRestoreField(active)) {
    passiveScannerSourceElement = active;
    passiveScannerSourceInitialValue = active.value;
  }

  if (passiveScannerBuffer && passiveScannerShouldRestoreField(active) && active === passiveScannerSourceElement) {
    event.preventDefault();
    event.stopPropagation();
  }

  passiveScannerBuffer += key;
  passiveScannerLastKeyAt = now;

  if (passiveScannerBuffer.length > 64) {
    passiveScannerBuffer = passiveScannerBuffer.slice(-64);
  }

  schedulePassiveScannerCommit(active);
}, true);

function cashierPreferenceScope() {
  return `cashier:${currentCashier?.username || currentCashier?.id || 'active'}`;
}

function applyCashierPreferenceScope() {
  if (!window.appI18n?.setPreferenceScope) return;
  if (currentCashier) {
    const identity = String(currentCashier.username || currentCashier.id || 'active');
    localStorage.setItem(LS_CASHIER_IDENTITY, identity);
    appI18n.setPreferenceScope(cashierPreferenceScope());
    return;
  }
  appI18n.setPreferenceScope('cashier:guest');
}

async function apiPostCashier(url, data) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    offlineAuth: 'cashier',
  });
}

async function apiPostCashierQueued(url, data, options = {}) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    offlineAuth: 'cashier',
    offlineQueue: true,
    offlineQueueScope: cashierPreferenceScope(),
    offlineInvalidateCacheKeys: options.invalidateCacheKeys || [],
    offlineReplayLabel: options.replayLabel || url,
  });
}

async function apiGetCashier(url, options = {}) {
  return fetch(url, {
    offlineAuth: 'cashier',
    offlineCacheKey: options.cacheKey || null,
    offlineCacheScope: cashierPreferenceScope(),
    offlineAllowStale: options.allowStale !== false,
  });
}

function setCashierUI() {
  const out = document.getElementById('cashierAuthLoggedOut');
  const inn = document.getElementById('cashierAuthLoggedIn');
  const content = document.getElementById('cashierAppContent');
  const shiftPanel = document.getElementById('cashierShiftPanel');
  if (currentCashier) {
    out.classList.add('d-none');
    inn.classList.remove('d-none');
    inn.classList.add('d-flex');
    document.getElementById('cashierBadge').textContent = currentCashier.name;
    content.style.display = '';
    if (shiftPanel) shiftPanel.classList.remove('d-none');
  } else {
    inn.classList.add('d-none');
    inn.classList.remove('d-flex');
    out.classList.remove('d-none');
    content.style.display = 'none';
    if (shiftPanel) shiftPanel.classList.add('d-none');
  }
  setCashierShiftUI();
}

async function initCashierAuth() {
  if (!cashierToken) {
    currentCashier = null;
    applyCashierPreferenceScope();
    setCashierUI();
    _initCashierLoginModal();
    return;
  }
  try {
    const r = await apiGetCashier('/api/cashiers/me');
    if (!r.ok) {
      if (r.status === 401 || r.status === 403) {
        cashierToken = null;
        localStorage.removeItem(LS_CASHIER_TOKEN);
      }
      currentCashier = null;
      setCashierUI();
      _initCashierLoginModal();
      if (r.status !== 401 && r.status !== 403) {
        showToast(tr('cashier_login_check_error'), 'warning');
      }
      applyCashierPreferenceScope();
      return;
    }
    currentCashier = await r.json();
    applyCashierPreferenceScope();
    setCashierUI();
    await loadCashierShiftStatus(true);
    _initCashierLoginModal();
  } catch {
    currentCashier = null;
    cashierShiftState = { has_active_shift: false, shift: null, summary: null };
    applyCashierPreferenceScope();
    setCashierUI();
    _initCashierLoginModal();
    showToast(tr('cashier_login_check_network_error'), 'warning');
  }
}

let _cashierLoginModalInited = false;
function _initCashierLoginModal() {
  if (_cashierLoginModalInited) return;
  _cashierLoginModalInited = true;

  const modalEl = document.getElementById('cashierLoginModal');
  if (!modalEl) return;

  modalEl.addEventListener('shown.bs.modal', () => {
    const u = document.getElementById('cashierUsername');
    if (u) u.focus();
  });

  ['cashierUsername', 'cashierPassword'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        cashierLogin();
      }
    });
  });
}

async function cashierLogin() {
  const username = document.getElementById('cashierUsername').value.trim();
  const password = document.getElementById('cashierPassword').value;
  if (!username || !password) {
    showToast(tr('cashier_login_fill'), 'warning');
    return;
  }
  const btn = document.getElementById('btnCashierLogin');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${tr('button_login')}...`;
  try {
    const r = await fetch('/api/cashiers/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      showToast(data.detail || tr('cashier_login_failed'), 'danger');
      return;
    }
    cashierToken = data.access_token;
    localStorage.setItem(LS_CASHIER_TOKEN, cashierToken);
    currentCashier = data.cashier;
    cashierShiftState = { has_active_shift: false, shift: null, summary: null };
    applyCashierPreferenceScope();
    document.getElementById('cashierPassword').value = '';
    setCashierUI();
    await loadCashierShiftStatus(true);

    const modalEl = document.getElementById('cashierLoginModal');
    if (modalEl) {
      try {
        bootstrap.Modal.getOrCreateInstance(modalEl).hide();
      } catch {}
    }

    showToast(`${tr('cashier_logged_in')}: ${currentCashier?.name || username}`, 'success');
  } catch {
    showToast(tr('cashier_connection_error'), 'danger');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i class="bi bi-box-arrow-in-right me-1"></i>${tr('button_login')}`;
  }
}

async function cashierLogout() {
  try {
    await apiPostCashier('/api/cashiers/logout', {});
  } catch {}
  cashierToken = null;
  localStorage.removeItem(LS_CASHIER_TOKEN);
  currentCashier = null;
  cashierShiftState = { has_active_shift: false, shift: null, summary: null };
  sessionOps = [];
  sessionOpsPage = 1;
  sessionOpsStorageKey = null;
  applyCashierPreferenceScope();
  setCashierUI();
  renderOps();
  window.location.href = '/staff-login?role=cashier&next=/cashier';
}