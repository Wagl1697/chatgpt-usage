import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

export function remaining(value) {
    return typeof value === 'number' && Number.isFinite(value) ? `${value}%` : '—';
}

export function countdown(reset, now = Date.now() / 1000) {
    if (typeof reset !== 'number' || !Number.isFinite(reset))
        return 'No disponible';
    const minutes = Math.max(0, Math.floor((reset - now) / 60));
    const days = Math.floor(minutes / 1440);
    const hours = Math.floor(minutes / 60) % 24;
    const rest = minutes % 60;
    return days ? `${days} d ${hours} h ${rest} min` : `${hours} h ${rest} min`;
}

export function resetLabel(resetAt) {
    if (typeof resetAt !== 'number' || !Number.isFinite(resetAt))
        return '⏰ —';
    const date = new Date(resetAt * 1000);
    const pad = value => String(value).padStart(2, '0');
    return `⏰ ${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${String(date.getFullYear()).slice(-2)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function resetExpiryLabel(value) {
    const millis = typeof value === 'string' ? Date.parse(value) : NaN;
    if (!Number.isFinite(millis))
        return 'Caducidad no disponible';
    const date = new Date(millis);
    const dateText = date.toLocaleString('es', {
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
        hourCycle: 'h23',
    }).replace(/\./g, '');
    const offset = -date.getTimezoneOffset();
    const sign = offset < 0 ? '-' : '+';
    const absolute = Math.abs(offset);
    const hours = Math.floor(absolute / 60);
    const minutes = absolute % 60;
    const timezone = `GMT${sign}${hours}${minutes ? `:${String(minutes).padStart(2, '0')}` : ''}`;
    return `Caduca el ${dateText} ${timezone}`;
}

export function accountTag(identity) {
    return String(identity || 'perfil').split('@', 1)[0];
}

export function setResetButtonEnabled(button, enabled) {
    // St.Button is a Clutter actor, not a Gtk.Widget (no set_sensitive()).
    button.reactive = Boolean(enabled);
    button.can_focus = Boolean(enabled);
    if (enabled)
        button.remove_style_pseudo_class('disabled');
    else
        button.add_style_pseudo_class('disabled');
}

export function carouselSources(accounts, goReport, displayNames = {}, configuredOrder = []) {
    const sources = (Array.isArray(accounts) ? accounts : []).map(account => {
        const email = String(account.email || account.label || 'Firefox profile');
        const id = `account:${email.toLocaleLowerCase()}`;
        return {
            id,
            kind: 'chatgpt',
            identity: displayNames[email.toLocaleLowerCase()] || accountTag(email),
            fresh: account.fresh === true,
            windows: account.windows || {},
        };
    });
    const go = goReport && typeof goReport === 'object' ? goReport : {};
    sources.push({
        id: 'opencode-go', kind: 'opencode', identity: displayNames['opencode-go'] || 'OpenCode Go',
        fresh: String(go.status || '').startsWith('OK'), windows: go.windows || {},
    });
    const order = Array.isArray(configuredOrder) ? configuredOrder : [];
    return sources.sort((a, b) => {
        const ai = order.indexOf(a.id);
        const bi = order.indexOf(b.id);
        const ar = ai < 0 ? (a.id === 'opencode-go' ? -1 : Number.MAX_SAFE_INTEGER) : ai;
        const br = bi < 0 ? (b.id === 'opencode-go' ? -1 : Number.MAX_SAFE_INTEGER) : bi;
        return ar - br || a.identity.localeCompare(b.identity);
    });
}

function makeRingDashboard(menu, definitions) {
    const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
    item.set_style('padding: 4px 6px;');
    const meters = definitions.map(([key, title]) => {
        const column = new St.BoxLayout({vertical: true, x_expand: true, y_expand: false, style_class: 'chatgpt-usage-meter'});
        column.set_style('min-width: 76px; padding: 1px 3px;');
        const heading = new St.Label({text: title, x_align: Clutter.ActorAlign.CENTER, x_expand: true, style_class: 'dim-label'});
        const ring = new St.DrawingArea({width: 32, height: 32, x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
        let percent = null;
        ring.connect('repaint', area => {
            const cr = area.get_context();
            const [width, height] = area.get_surface_size();
            const size = Math.min(width, height);
            const center = size / 2;
            const radius = center - 3;
            const start = -Math.PI / 2;
            cr.setLineWidth(4);
            cr.setSourceRGBA(0.48, 0.48, 0.48, 0.42);
            cr.arc(center, center, radius, 0, Math.PI * 2);
            cr.stroke();
            const fraction = typeof percent === 'number' ? Math.max(0, Math.min(100, percent)) / 100 : 0;
            const color = fraction <= 0.15 ? [0.95, 0.28, 0.25] : fraction <= 0.4 ? [0.98, 0.66, 0.18] : [0.35, 0.78, 0.48];
            if (typeof percent === 'number' && fraction > 0) {
                cr.setSourceRGBA(...color, 1);
                cr.arc(center, center, radius, start, start + Math.PI * 2 * fraction);
                cr.stroke();
            }
            cr.$dispose();
        });
        const value = new St.Label({text: '—', x_align: Clutter.ActorAlign.CENTER, x_expand: true, style_class: 'caption'});
        const reset = new St.Label({text: '—', x_align: Clutter.ActorAlign.CENTER, x_expand: true, style_class: 'caption'});
        column.add_child(heading);
        column.add_child(ring);
        column.add_child(value);
        column.add_child(reset);
        item.add_child(column);
        return {key, ring, value, reset, setPercent(value) {
            percent = typeof value === 'number' && Number.isFinite(value) ? value : null;
            ring.queue_repaint();
            this.value.set_text(remaining(percent));
        }, setReset(text) { this.reset.set_text(text); }};
    });
    menu.addMenuItem(item);
    return {item, update(windows, now) {
        for (const meter of meters) {
            const data = windows?.[meter.key] || {};
            meter.setPercent(data.remaining_percent);
            const resetText = resetLabel(data.reset_at);
            meter.setReset(data.limited ? `Alcanzado · ${resetText}` : resetText);
        }
    }};
}

export default class ChatGPTPlanMonitor extends Extension {
    enable() {
        this._stopped = false;
        this._reading = false;
        this._carouselIndex = 0;
        this._lastReport = {};
        this._consumedResetCredits = new Set();
        this._settings = this.getSettings();
        this._displayNames = this._readDisplayNames();
        this._carouselOrder = this._readCarouselOrder();
        this._showHoverSummary = this._settings.get_boolean('show-hover-summary');
        this._cancellable = new Gio.Cancellable();
        const stateHome = GLib.getenv('XDG_STATE_HOME') || `${GLib.get_home_dir()}/.local/state`;
        this._statePath = this.metadata['state-file'] || `${stateHome}/chatgpt-plan-monitor/uso.json`;
        this._file = Gio.File.new_for_path(this._statePath);
        this._button = new PanelMenu.Button(0.0, 'Uso del plan de ChatGPT');
        this._label = new St.Label({text: 'GPT …', y_align: Clutter.ActorAlign.CENTER});
        this._button.add_child(this._label);
        this._status = this._row('Esperando datos');
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._pollingEnabled = this._readPollingEnabled();
        this._pollSwitch = new PopupMenu.PopupSwitchMenuItem('Actualizar límites automáticamente', this._pollingEnabled);
        this._button.menu.addMenuItem(this._pollSwitch);
        this._pollSwitch.connect('toggled', (_item, enabled) => this._setPollingEnabled(enabled));
        this._button.menu.addAction('Preferencias…', () => this.openPreferences());
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._chatgptSection = new PopupMenu.PopupMenuSection();
        this._button.menu.addMenuItem(this._chatgptSection);
        this._accountRows = new Map();
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._goStatus = this._row('Esperando datos de OpenCode Go');
        this._goDashboard = makeRingDashboard(this._button.menu, [
            ['five_hour', '5 horas'], ['weekly', 'Semanal'], ['monthly', 'Mensual'],
        ]);
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._updated = this._row('Última lectura · —');
        this._updated.label.x_align = Clutter.ActorAlign.CENTER;
        this._updated.label.set_style('font-size: 0.82em; text-align: center;');
        this._button.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._refresh();
            else
                this._clearResetConfirmations();
            this._syncTooltip();
        });
        this._tooltip = new St.Label({style_class: 'chatgpt-usage-tooltip', visible: false, reactive: false, x_align: Clutter.ActorAlign.START});
        this._tooltip.clutter_text.line_wrap = true;
        this._tooltip.clutter_text.line_alignment = Pango.Alignment.LEFT;
        this._tooltip.set_style('padding: 8px 12px; max-width: 460px; border-radius: 5px; text-align: left;');
        Main.uiGroup.add_child(this._tooltip);
        this._button.connect('notify::hover', () => this._syncTooltip());
        this._settingsSignals = [
            this._settings.connect('changed::account-display-names', () => {
                this._displayNames = this._readDisplayNames();
                this._render(this._lastReport);
            }),
            this._settings.connect('changed::carousel-order', () => {
                this._carouselOrder = this._readCarouselOrder();
                this._render(this._lastReport);
            }),
            this._settings.connect('changed::carousel-interval', () => this._resetCarouselTimer()),
            this._settings.connect('changed::show-hover-summary', () => {
                this._showHoverSummary = this._settings.get_boolean('show-hover-summary');
                this._syncTooltip();
            }),
        ];
        const monitor = Main.layoutManager.primaryMonitor;
        this._menuScroll = new St.ScrollView({
            style_class: 'chatgpt-usage-menu-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.ALWAYS,
            x_expand: true,
            y_expand: true,
        });
        this._menuScroll.set_style(`max-height: ${Math.floor(monitor.height * 0.82)}px; max-width: ${Math.floor(monitor.width * 0.58)}px;`);
        const menuBin = this._button.menu._boxPointer.bin;
        menuBin.set_child(null);
        this._menuScroll.set_child(this._button.menu.box);
        menuBin.set_child(this._menuScroll);
        Main.panel.addToStatusArea(this.uuid, this._button, 1, 'left');
        this._refresh();
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._resetCarouselTimer();
    }

    _readDisplayNames() {
        try {
            const parsed = JSON.parse(this._settings.get_string('account-display-names'));
            return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
        } catch (error) {
            return {};
        }
    }

    _readCarouselOrder() {
        try {
            const parsed = JSON.parse(this._settings.get_string('carousel-order'));
            return Array.isArray(parsed) ? parsed.filter(value => typeof value === 'string') : [];
        } catch (error) {
            return [];
        }
    }

    _readPollingEnabled() {
        try {
            const path = Gio.File.new_for_path(GLib.path_get_dirname(this._statePath)).get_child('polling.json');
            const [, bytes] = path.load_contents(null);
            return JSON.parse(new TextDecoder().decode(bytes)).enabled !== false;
        } catch (error) {
            return true;
        }
    }

    _setPollingEnabled(enabled) {
        this._pollingEnabled = enabled;
        const path = Gio.File.new_for_path(GLib.path_get_dirname(this._statePath)).get_child('polling.json');
        const contents = new TextEncoder().encode(JSON.stringify({enabled}) + '\n');
        path.replace_contents_bytes_async(
            GLib.Bytes.new(contents), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION,
            this._cancellable, (_file, result) => {
                try {
                    path.replace_contents_finish(result);
                } catch (error) {
                    this._pollingEnabled = this._readPollingEnabled();
                    this._render(this._lastReport);
                    Main.notify('Monitor de uso', 'No se pudo guardar el estado de las consultas.');
                }
            });
        this._render(this._lastReport);
    }

    _resetCarouselTimer() {
        if (this._carouselTimer)
            GLib.source_remove(this._carouselTimer);
        const interval = Math.max(3, Math.min(60, this._settings.get_int('carousel-interval')));
        this._carouselTimer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, interval, () => {
            this._advanceCarousel();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _orderedSources(accounts, goReport) {
        return carouselSources(accounts, goReport, this._displayNames, this._carouselOrder);
    }

    _row(text, menu = this._button.menu) {
        const item = new PopupMenu.PopupMenuItem(text, {reactive: false, can_focus: false});
        menu.addMenuItem(item);
        return item;
    }

    _updateTooltip(accounts, goWindows, goFresh) {
        const lines = [];
        for (const account of accounts) {
            const windows = account.windows || {};
            lines.push(`${account.identity}: 5 h ${remaining(windows.five_hour?.remaining_percent)} ${resetLabel(windows.five_hour?.reset_at)} · semanal ${remaining(windows.weekly?.remaining_percent)} ${resetLabel(windows.weekly?.reset_at)}`);
        }
        lines.push(`${this._displayNames['opencode-go'] || 'OpenCode Go'}${goFresh ? '' : ' ⚠'}: 5 h ${remaining(goWindows.five_hour?.remaining_percent)} ${resetLabel(goWindows.five_hour?.reset_at)} · semanal ${remaining(goWindows.weekly?.remaining_percent)} ${resetLabel(goWindows.weekly?.reset_at)} · mensual ${remaining(goWindows.monthly?.remaining_percent)} ${resetLabel(goWindows.monthly?.reset_at)}`);
        this._tooltipText = lines.join('\n');
    }

    _syncTooltip() {
        if (!this._tooltip || !this._button || this._stopped)
            return;
        const show = this._showHoverSummary && this._button.hover && !this._button.menu.isOpen;
        if (show) {
            this._tooltip.set({text: this._tooltipText || 'Cargando límites…', visible: true, opacity: 0});
            const monitor = Main.layoutManager.findMonitorForActor(this._button) || Main.layoutManager.primaryMonitor;
            const [stageX, stageY] = this._button.get_transformed_position();
            const buttonWidth = this._button.allocation.get_width();
            const buttonHeight = this._button.allocation.get_height();
            const width = Math.min(Math.max(260, Math.floor(monitor.width * 0.32)), Math.floor(monitor.width * 0.65));
            this._tooltip.set_width(width);
            const [, height] = this._tooltip.get_preferred_height(width);
            const x = Math.clamp(stageX + (buttonWidth - width) / 2, monitor.x, monitor.x + monitor.width - width);
            let y = stageY + buttonHeight + 6;
            if (y + height > monitor.y + monitor.height)
                y = Math.max(monitor.y, stageY - height - 6);
            this._tooltip.set_position(x, y);
        }
        this._tooltip.ease({
            opacity: show ? 255 : 0,
            duration: 120,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                if (this._tooltip)
                    this._tooltip.visible = show && Boolean(this._button?.hover) && !Boolean(this._button?.menu.isOpen);
            },
        });
    }

    _refresh() {
        if (this._reading || this._stopped)
            return;
        this._reading = true;
        this._file.load_contents_async(this._cancellable, (file, result) => {
            this._reading = false;
            if (this._stopped)
                return;
            let report = {};
            try {
                const [, contents] = file.load_contents_finish(result);
                report = JSON.parse(new TextDecoder().decode(contents));
            } catch (error) {
                // Missing/corrupt reports must never crash GNOME Shell.
            }
            this._render(report && typeof report === 'object' ? report : {});
        });
    }

    _render(report, carouselTick = false) {
        if (!carouselTick)
            this._lastReport = report;
        const now = Date.now() / 1000;
        const legacyWindows = report.windows && typeof report.windows === 'object' ? report.windows : {};
        const sourceAccounts = Array.isArray(report.chatgpt_accounts) && report.chatgpt_accounts.length
            ? report.chatgpt_accounts
            : [{label: 'ChatGPT', status: report.status, fetched_at: report.fetched_at,
                windows: {five_hour: legacyWindows.five_hour, weekly: legacyWindows.weekly}}];
        const accounts = sourceAccounts.map(account => {
            const fetchedAt = account.fetched_at;
            const fresh = this._pollingEnabled && report.polling_enabled !== false && account.status === 'OK' && typeof fetchedAt === 'number' && now - fetchedAt >= -5 && now - fetchedAt <= 90;
            const email = String(account.email || account.label || 'Perfil Firefox');
            const emailKey = email.toLocaleLowerCase();
            return {...account, email, fresh, identity: this._displayNames[emailKey] || accountTag(email)};
        }).sort((a, b) => a.identity.localeCompare(b.identity));
        const chatgptFresh = this._pollingEnabled && report.polling_enabled !== false && accounts.length > 0 && accounts.every(account => account.fresh);
        const goReport = report.opencode_go && typeof report.opencode_go === 'object' ? report.opencode_go : {};
        const goWindows = goReport.windows && typeof goReport.windows === 'object' ? goReport.windows : {};
        const goAge = now - goReport.fetched_at;
        const goFresh = report.polling_enabled !== false && this._pollingEnabled && typeof goReport.fetched_at === 'number' && goAge >= -5 && goAge <= 150 &&
            typeof goReport.status === 'string' && goReport.status.startsWith('OK');
        this._carouselSources = this._orderedSources(accounts, goReport).map(source => ({
            ...source, fresh: source.kind === 'opencode' ? goFresh : source.fresh,
        }));
        if (this._carouselSources.length)
            this._carouselIndex %= this._carouselSources.length;
        const current = this._carouselSources[this._carouselIndex];
        const meter = current?.windows?.five_hour || {};
        const paused = !this._pollingEnabled || report.polling_enabled === false;
        const name = current?.kind === 'opencode' ? current.identity : (current?.identity || 'ChatGPT');
        this._label.set_text(`${paused ? '⏸ ' : ''}${name}${!paused && current && !current.fresh ? ' ⚠' : ''} 5h ${remaining(meter.remaining_percent)}`);
        this._pollSwitch.setToggleState(this._pollingEnabled);
        this._status.label.set_text(!this._pollingEnabled ? '⏸ Consultas pausadas — valores anteriores' : chatgptFresh && goFresh ? '● ChatGPT y OpenCode Go actualizados' : '⚠ Hay datos sin actualizar — ver estados abajo');
        const seenAccounts = new Set();
        let resetRenderFailed = false;
        for (const account of accounts) {
            const key = String(account.email || account.label || account.identity).toLocaleLowerCase();
            seenAccounts.add(key);
            let rows = this._accountRows.get(key);
            if (!rows) {
                const section = new PopupMenu.PopupMenuSection();
                this._chatgptSection.addMenuItem(section);
                section.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
                rows = {
                    section,
                    heading: this._row('', section), status: this._row('', section),
                    dashboard: makeRingDashboard(section, [['five_hour', '5 horas'], ['weekly', 'Semanal']]),
                    resetHeader: this._row('', section),
                    resetItems: new Map(),
                };
                rows.resetHeader.hide();
                this._accountRows.set(key, rows);
            }
            rows.heading.label.set_text(`ChatGPT · ${account.identity}`);
            rows.status.label.set_text(account.fresh ? `● Actualizado · plan ${account.plan_type || 'ChatGPT'}` : `⚠ ${account.status || 'Sin datos actuales'}`);
            rows.dashboard.update(account.windows, now);
            try {
                this._updateResetCredits(rows, account);
            } catch (error) {
                // Optional reset controls must not stop other accounts or Go.
                resetRenderFailed = true;
                rows.resetHeader.label.set_text('⚠ No se pudieron mostrar los restablecimientos');
                rows.resetHeader.show();
                console.error('ChatGPT usage: error rendering reset controls', error);
            }
        }
        for (const [key, rows] of this._accountRows) {
            if (!seenAccounts.has(key)) {
                for (const resetRow of rows.resetItems?.values() || [])
                    resetRow.destroyed = true;
                rows.section.destroy();
                this._accountRows.delete(key);
            }
        }
        const reads = accounts.map(account => account.fetched_at).filter(value => typeof value === 'number');
        if (typeof goReport.fetched_at === 'number')
            reads.push(goReport.fetched_at);
        const latest = reads.length ? new Date(Math.max(...reads) * 1000) : null;
        const latestText = latest
            ? `${String(latest.getDate()).padStart(2, '0')}/${String(latest.getMonth() + 1).padStart(2, '0')}/${String(latest.getFullYear()).slice(-2)} ${String(latest.getHours()).padStart(2, '0')}:${String(latest.getMinutes()).padStart(2, '0')}`
            : '—';
        this._updated.label.set_text(`${this._pollingEnabled ? '' : '⏸ ' }Última lectura · ${latestText}`);
        this._goStatus.label.set_text(goFresh ? '● OpenCode Go actualizado' : `⚠ ${goReport.status || 'Esperando primera consulta de OpenCode Go'}`);
        this._goDashboard.update(goWindows, now);
        if (resetRenderFailed)
            this._status.label.set_text('⚠ Límites cargados; error al mostrar restablecimientos');
        this._updateTooltip(accounts, goWindows, goFresh);
        if (this._button.hover && !this._button.menu.isOpen)
            this._syncTooltip();
    }

    _advanceCarousel() {
        const sources = this._carouselSources || [];
        if (sources.length < 2)
            return;
        this._carouselIndex = (this._carouselIndex + 1) % sources.length;
        this._render(this._lastReport, true);
    }

    _updateResetCredits(rows, account) {
        const email = typeof account.email === 'string' ? account.email : '';
        const credits = (email && Array.isArray(account.reset_credits) ? account.reset_credits : [])
            .filter(credit => credit && typeof credit.id === 'string' &&
                !this._consumedResetCredits.has(`${email.toLocaleLowerCase()}\u0000${credit.id}`));
        rows.resetHeader.label.set_text('No se pudieron consultar los restablecimientos');
        rows.resetHeader.visible = Boolean(account.reset_credits_status);
        const visibleIds = new Set(credits.map(credit => credit.id));
        for (const [id, row] of rows.resetItems) {
            if (visibleIds.has(id))
                continue;
            row.destroyed = true;
            row.item.destroy();
            rows.resetItems.delete(id);
        }

        for (const credit of credits) {
            let row = rows.resetItems.get(credit.id);
            if (!row) {
                const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
                item.set_style('padding: 1px 6px;');
                const box = new St.BoxLayout({x_expand: true, y_expand: false, style_class: 'chatgpt-usage-reset-row'});
                const labels = new St.BoxLayout({vertical: true, x_expand: true, y_expand: false, style_class: 'chatgpt-usage-reset-labels'});
                const title = new St.Label({text: '', x_align: Clutter.ActorAlign.START, x_expand: true});
                const expiry = new St.Label({text: '', x_align: Clutter.ActorAlign.START, x_expand: true, style_class: 'chatgpt-usage-reset-expiry'});
                const button = new St.Button({
                    label: 'Usar',
                    can_focus: true,
                    reactive: true,
                    track_hover: true,
                    style_class: 'chatgpt-usage-reset-button',
                    x_align: Clutter.ActorAlign.END,
                    y_align: Clutter.ActorAlign.CENTER,
                    x_expand: false,
                    y_expand: false,
                });
                labels.add_child(title);
                labels.add_child(expiry);
                box.add_child(labels);
                box.add_child(button);
                item.add_child(box);
                rows.section.addMenuItem(item);
                row = {item, title, expiry, button, credit, email: account.email, confirmed: false, loading: false, destroyed: false};
                button.connect('clicked', () => this._onResetButton(row));
                rows.resetItems.set(credit.id, row);
            }
            row.credit = credit;
            row.email = email;
            row.fresh = account.fresh;
            row.title.set_text(credit.title || 'Restablecimiento de límites');
            row.expiry.set_text(resetExpiryLabel(credit.expires_at));
            if (row.loading)
                row.button.set_label('Aplicando…');
            else if (!credit.is_supported_by_plan)
                row.button.set_label('No disponible');
            else
                row.button.set_label(row.confirmed ? 'Confirmar' : 'Usar');
            setResetButtonEnabled(row.button, account.fresh && credit.is_supported_by_plan && !row.loading);
        }
    }

    _clearResetConfirmations() {
        for (const rows of this._accountRows?.values() || []) {
            for (const row of rows.resetItems.values()) {
                row.confirmed = false;
                if (!row.loading)
                    row.button.set_label(row.credit.is_supported_by_plan ? 'Usar' : 'No disponible');
            }
        }
    }

    _onResetButton(row) {
        if (this._stopped || row.destroyed || row.loading || !row.fresh ||
            !row.button.reactive || !row.email || !row.credit.is_supported_by_plan)
            return;
        if (!row.confirmed) {
            row.confirmed = true;
            row.button.set_label('Confirmar');
            return;
        }
        const python = this.metadata['scan-python'];
        const script = this.metadata['reset-script'];
        if (!python || !script) {
            Main.notify('Restablecimiento de ChatGPT', 'Falta configurar el ayudante de restablecimientos.');
            return;
        }
        let launcher;
        let process;
        try {
            launcher = new Gio.SubprocessLauncher({
                flags: Gio.SubprocessFlags.STDIN_PIPE |
                    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            });
            const schemaDir = this.dir.get_child('schemas').get_path();
            launcher.setenv('CHATGPT_USAGE_SCHEMA_DIR', schemaDir, true);
            process = launcher.spawnv([python, script]);
        } catch (error) {
            Main.notify('Restablecimiento de ChatGPT', 'No se pudo iniciar la operación.');
            return;
        }
        row.loading = true;
        setResetButtonEnabled(row.button, false);
        row.button.set_label('Aplicando…');
        const input = JSON.stringify({email: row.email, credit_id: row.credit.id, confirmed: true});
        process.communicate_utf8_async(input, this._cancellable, (proc, result) => {
            let message = 'No se pudo completar el restablecimiento.';
            let success = false;
            let code = null;
            try {
                const [, stdout] = proc.communicate_utf8_finish(result);
                const response = JSON.parse(stdout || '{}');
                message = typeof response.message === 'string' ? response.message : message;
                success = response.ok === true;
                code = response.code || null;
            } catch (error) {
                // Keep the UI alive if the helper exits without a valid response.
            }
            if (this._stopped)
                return;
            if (success && code)
                this._consumedResetCredits.add(`${row.email.toLocaleLowerCase()}\u0000${row.credit.id}`);
            row.loading = false;
            row.confirmed = false;
            if (!row.destroyed) {
                row.button.set_label(row.credit.is_supported_by_plan ? 'Usar' : 'No disponible');
                setResetButtonEnabled(row.button, row.fresh && row.credit.is_supported_by_plan);
            }
            Main.notify('Restablecimiento de ChatGPT', message);
            if (success)
                this._refresh();
        });
    }

    disable() {
        this._stopped = true;
        this._cancellable?.cancel();
        if (this._timer)
            GLib.source_remove(this._timer);
        if (this._carouselTimer)
            GLib.source_remove(this._carouselTimer);
        this._timer = null;
        this._carouselTimer = null;
        this._tooltip?.destroy();
        this._tooltip = null;
        this._button?.destroy();
        this._button = null;
        this._accountRows?.clear();
        this._menuScroll = null;
        for (const signal of this._settingsSignals || [])
            this._settings.disconnect(signal);
        this._settingsSignals = [];
        this._settings = null;
        this._file = null;
    }
}
