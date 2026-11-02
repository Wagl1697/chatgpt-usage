import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class ChatGPTUsagePreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const page = new Adw.PreferencesPage({title: 'Barra de uso', icon_name: 'view-statistics-symbolic'});

        const carouselGroup = new Adw.PreferencesGroup({
            title: 'Carrusel de la barra',
            description: 'El menú conserva todas las fuentes. La barra rota una fuente por vez.',
        });
        const intervalRow = new Adw.ActionRow({
            title: 'Segundos entre fuentes',
            subtitle: 'Tiempo que permanece cada cuenta o proveedor en la barra.',
        });
        const interval = Gtk.SpinButton.new_with_range(3, 60, 1);
        interval.set_value(settings.get_int('carousel-interval'));
        interval.connect('value-changed', () => settings.set_int('carousel-interval', interval.get_value_as_int()));
        intervalRow.add_suffix(interval);
        intervalRow.set_activatable_widget(interval);
        carouselGroup.add(intervalRow);
        const hoverSummary = new Adw.SwitchRow({
            title: 'Resumen al pasar el cursor',
            subtitle: 'Muestra las cuotas al dejar el puntero sobre la barra.',
        });
        settings.bind('show-hover-summary', hoverSummary, 'active', Gio.SettingsBindFlags.DEFAULT);
        carouselGroup.add(hoverSummary);

        const sources = this._sources(settings);
        if (sources.length) {
            const savedOrder = this._readOrder(settings);
            const order = savedOrder.length ? savedOrder : ['opencode-go', ...sources.map(source => source.id).filter(id => id !== 'opencode-go')];
            const ranked = [...sources].sort((a, b) => {
                const ai = order.indexOf(a.id);
                const bi = order.indexOf(b.id);
                return (ai < 0 ? Number.MAX_SAFE_INTEGER : ai) - (bi < 0 ? Number.MAX_SAFE_INTEGER : bi)
                    || a.title.localeCompare(b.title);
            });
            const positions = new Map(ranked.map((source, index) => [source.id, index + 1]));
            const spins = new Map();
            let adjusting = false;
            for (const source of sources) {
                const row = new Adw.ActionRow({title: source.title, subtitle: source.subtitle});
                const spin = Gtk.SpinButton.new_with_range(1, sources.length, 1);
                spin.set_value(positions.get(source.id));
                spins.set(source.id, spin);
                spin.set_tooltip_text('Posición en el carrusel');
                spin.connect('value-changed', () => {
                    if (adjusting)
                        return;
                    const oldPosition = positions.get(source.id);
                    const newPosition = spin.get_value_as_int();
                    const collision = sources.find(item => item.id !== source.id && positions.get(item.id) === newPosition);
                    adjusting = true;
                    if (collision) {
                        positions.set(collision.id, oldPosition);
                        spins.get(collision.id)?.set_value(oldPosition);
                    }
                    positions.set(source.id, newPosition);
                    adjusting = false;
                    const reordered = [...sources].sort((a, b) => {
                        const first = positions.get(a.id);
                        const second = positions.get(b.id);
                        return first - second || a.title.localeCompare(b.title);
                    }).map(item => item.id);
                    settings.set_string('carousel-order', JSON.stringify(reordered));
                });
                row.add_suffix(spin);
                row.set_activatable_widget(spin);
                carouselGroup.add(row);
            }
        } else {
            carouselGroup.add(new Adw.ActionRow({
                title: 'OpenCode Go',
                subtitle: 'Las cuentas de ChatGPT aparecerán al iniciar sesión en Firefox.',
            }));
        }
        page.add(carouselGroup);

        const accountsGroup = new Adw.PreferencesGroup({
            title: 'Cuentas controladas',
            description: 'El monitor consulta solo las cuentas activas. Las cookies no se guardan.',
        });
        const readAccounts = () => {
            try {
                const parsed = JSON.parse(settings.get_string('tracked-accounts'));
                const list = Array.isArray(parsed) ? parsed : parsed?.accounts;
                return Array.isArray(list) ? list : [];
            } catch (error) {
                return [];
            }
        };
        let accounts = readAccounts();
        let scanning = false;
        const accountRows = [];
        const saveAccounts = () => settings.set_string('tracked-accounts', JSON.stringify({accounts}));

        const renderAccounts = status => {
            for (const row of accountRows)
                accountsGroup.remove(row);
            accountRows.length = 0;

            const refresh = new Adw.ActionRow({
                title: 'Actualizar detección',
                subtitle: status || 'Busca sesiones de ChatGPT en los navegadores instalados.',
            });
            const button = new Gtk.Button({label: 'Actualizar', valign: Gtk.Align.CENTER});
            button.set_sensitive(!scanning);
            button.connect('clicked', () => runScan());
            refresh.add_suffix(button);
            refresh.set_activatable_widget(button);
            accountsGroup.add(refresh);
            accountRows.push(refresh);

            if (!accounts.length) {
                const empty = new Adw.ActionRow({
                    title: 'Sin cuentas configuradas',
                    subtitle: 'Pulsá «Actualizar» para detectar sesiones de ChatGPT.',
                });
                accountsGroup.add(empty);
                accountRows.push(empty);
                return;
            }

            for (const account of accounts) {
                const sources = (account.sources || [])
                    .map(source => `${source.browser} · ${source.profile}`)
                    .join(', ');
                const row = new Adw.SwitchRow({
                    title: account.email,
                    subtitle: sources || 'Sin fuente guardada',
                    active: account.enabled !== false,
                });
                row.connect('notify::active', () => {
                    account.enabled = row.active;
                    saveAccounts();
                });
                accountsGroup.add(row);
                accountRows.push(row);
            }
        };

        const mergeScan = data => {
            const detected = Array.isArray(data.accounts) ? data.accounts : [];
            const prior = new Map(accounts.map(account => [String(account.email).toLocaleLowerCase(), account]));
            const firstRun = accounts.length === 0;
            const seen = new Set();
            const merged = [];
            for (const account of detected) {
                const email = String(account.email || '').trim();
                if (!email)
                    continue;
                const key = email.toLocaleLowerCase();
                if (seen.has(key))
                    continue;
                seen.add(key);
                const old = prior.get(key);
                merged.push({
                    email,
                    enabled: old ? old.enabled !== false : firstRun,
                    sources: Array.isArray(account.sources) ? account.sources : [],
                });
            }
            for (const account of accounts) {
                const key = String(account.email).toLocaleLowerCase();
                if (seen.has(key))
                    continue;
                seen.add(key);
                merged.push(account);
            }
            accounts = merged;
            saveAccounts();
        };

        const runScan = () => {
            if (scanning)
                return;
            const script = this.metadata['scan-script'];
            const python = this.metadata['scan-python'];
            if (!script || !python) {
                renderAccounts('Falta configurar scan-script/scan-python en metadata.json.');
                return;
            }
            scanning = true;
            renderAccounts('Buscando sesiones de ChatGPT…');
            let process;
            try {
                process = Gio.Subprocess.new(
                    [python, script],
                    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
                );
            } catch (error) {
                scanning = false;
                renderAccounts(`No se pudo iniciar la detección: ${error.message}`);
                return;
            }
            process.communicate_utf8_async(null, null, (proc, result) => {
                scanning = false;
                try {
                    const [ok, stdout, stderr] = proc.communicate_utf8_finish(result);
                    if (!ok)
                        throw new Error((stderr || '').trim() || 'La detección falló.');
                    const data = JSON.parse(stdout);
                    mergeScan(data);
                    const missing = Array.isArray(data.unavailable) ? data.unavailable.length : 0;
                    renderAccounts(`Detección completada: ${accounts.length} cuenta(s)` +
                        (missing ? `; ${missing} perfil(es) sin sesión.` : '.'));
                } catch (error) {
                    renderAccounts(`No se pudo detectar: ${error.message}`);
                }
            });
        };

        renderAccounts();
        page.add(accountsGroup);

        const namesGroup = new Adw.PreferencesGroup({
            title: 'Nombres de las fuentes',
            description: 'Nombres visibles en el carrusel, hover y menú. Solo cambian la etiqueta, no la cuenta medida.',
        });
        const names = this._readNames(settings);
        for (const source of this._sources(settings)) {
            const key = source.id === 'opencode-go' ? 'opencode-go' : source.email.toLocaleLowerCase();
            const defaultName = source.id === 'opencode-go' ? 'OpenCode Go' : source.email.split('@', 1)[0];
            const row = new Adw.ActionRow({title: source.id === 'opencode-go' ? 'OpenCode Go' : source.email, subtitle: 'Nombre visible en barra, hover y menú'});
            const entry = new Gtk.Entry({
                text: names[key] || defaultName,
                placeholder_text: defaultName,
                width_chars: 16,
                max_length: 40,
                valign: Gtk.Align.CENTER,
            });
            entry.connect('changed', () => {
                const updated = this._readNames(settings);
                const value = entry.get_text().trim();
                if (value && value !== defaultName)
                    updated[key] = value;
                else
                    delete updated[key];
                settings.set_string('account-display-names', JSON.stringify(updated));
            });
            row.add_suffix(entry);
            row.set_activatable_widget(entry);
            namesGroup.add(row);
        }
        page.add(namesGroup);
        window.add(page);
    }

    _readOrder(settings) {
        try {
            const result = JSON.parse(settings.get_string('carousel-order'));
            return Array.isArray(result) ? result : [];
        } catch (error) {
            return [];
        }
    }

    _readNames(settings) {
        try {
            const result = JSON.parse(settings.get_string('account-display-names'));
            return result && typeof result === 'object' && !Array.isArray(result) ? result : {};
        } catch (error) {
            return {};
        }
    }

    _chatgptAccounts() {
        try {
            const stateHome = GLib.getenv('XDG_STATE_HOME') || `${GLib.get_home_dir()}/.local/state`;
            const reportPath = this.metadata['state-file'] || `${stateHome}/chatgpt-plan-monitor/uso.json`;
            const file = Gio.File.new_for_path(reportPath);
            const [, bytes] = file.load_contents(null);
            const report = JSON.parse(new TextDecoder().decode(bytes));
            return Array.isArray(report.chatgpt_accounts) ? report.chatgpt_accounts : [];
        } catch (error) {
            return [];
        }
    }

    _sources(settings) {
        const accounts = this._chatgptAccounts();
        const sources = accounts.map(account => {
            const email = String(account.email || account.label || 'Perfil Firefox');
            return {id: `account:${email.toLocaleLowerCase()}`, email, title: this._readNames(settings)[email.toLocaleLowerCase()] || email.split('@', 1)[0], subtitle: 'Límites de ChatGPT'};
        });
        sources.push({id: 'opencode-go', title: this._readNames(settings)['opencode-go'] || 'OpenCode Go', subtitle: '5 horas, semanal y mensual'});
        return sources;
    }
}
