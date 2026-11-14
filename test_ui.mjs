import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// St actors deliberately have no Gtk.Widget.set_sensitive(): the old call
// aborted _render() before the second account and OpenCode Go were updated.
class Actor {
    constructor(properties = {}) {
        this.children = [];
        this.signals = new Map();
        this.pseudoClasses = new Set();
        this.visible = true;
        Object.assign(this, properties);
    }

    add_child(actor) { this.children.push(actor); }
    addMenuItem(item) { this.add_child(item); }
    set_style(style) { this.style = style; }
    set_text(text) { this.text = text; }
    set_label(label) { this.label = label; }
    queue_repaint() {}
    connect(signal, callback) { this.signals.set(signal, callback); }
    add_style_pseudo_class(name) { this.pseudoClasses.add(name); }
    remove_style_pseudo_class(name) { this.pseudoClasses.delete(name); }
    show() { this.visible = true; }
    hide() { this.visible = false; }
    destroy() { this.destroyed = true; }
}

class MenuItem extends Actor {
    constructor(text, properties = {}) {
        super(properties);
        this.label = new Actor({text});
    }
}

const source = readFileSync(new URL('./gnome-extension/extension.js', import.meta.url), 'utf8');
const notifications = [];
let subprocessCount = 0;
const context = {
    Clutter: {ActorAlign: {START: 'start', END: 'end', CENTER: 'center'}},
    Gio: {SubprocessLauncher: class {
        constructor() { subprocessCount++; throw new Error('No real subprocesses in UI tests'); }
    }, SubprocessFlags: {STDIN_PIPE: 1, STDOUT_PIPE: 2, STDERR_PIPE: 4}},
    GLib: {},
    Pango: {},
    St: {BoxLayout: Actor, Label: Actor, DrawingArea: Actor, Button: Actor},
    Extension: class {},
    Main: {notify: (...args) => notifications.push(args)},
    PanelMenu: {},
    PopupMenu: {
        PopupBaseMenuItem: Actor,
        PopupMenuItem: MenuItem,
        PopupMenuSection: Actor,
        PopupSeparatorMenuItem: Actor,
    },
    console: {error() {}},
};
const {Monitor, makeRingDashboard, setResetButtonEnabled, clamp} = vm.runInNewContext(
    source.replace(/^import .*;\n/gm, '').replace(/export default class /, 'class ')
        .replace(/export function /g, 'function ') +
        '\n({Monitor: ChatGPTPlanMonitor, makeRingDashboard, setResetButtonEnabled: ' +
        'typeof setResetButtonEnabled === "function" ? setResetButtonEnabled : undefined, ' +
        'clamp: typeof clamp === "function" ? clamp : undefined});',
    context,
    {filename: 'gnome-extension/extension.js'},
);

test('clamp replaces the unavailable Math.clamp', () => {
    assert.equal(clamp(-5, 0, 10), 0);
    assert.equal(clamp(15, 0, 10), 10);
    assert.equal(clamp(5, 0, 10), 5);
});

test('the extension never calls Math.clamp', () => {
    assert.ok(!source.includes('Math.clamp('),
        'Math.clamp is undefined in GNOME Shell and breaks the hover tooltip');
});

function renderer() {
    const instance = new Monitor();
    Object.assign(instance, {
        metadata: {'scan-python': '/test/python', 'reset-script': '/test/reset.py'},
        _stopped: false,
        _pollingEnabled: true,
        _displayNames: {},
        _carouselOrder: [],
        _carouselIndex: 0,
        _accountRows: new Map(),
        _consumedResetCredits: new Set(),
        _button: {menu: new Actor(), hover: false},
        _label: new Actor(),
        _pollSwitch: {setToggleState() {}},
        _status: new MenuItem(''),
        _goStatus: new MenuItem(''),
        _updated: new MenuItem(''),
        _chatgptSection: new Actor(),
    });
    instance._goDashboard = makeRingDashboard(instance._button.menu, [
        ['five_hour', '5 horas'], ['weekly', 'Semanal'], ['monthly', 'Mensual'],
    ]);
    return instance;
}

function report() {
    const now = Date.now() / 1000;
    return {
        polling_enabled: true,
        chatgpt_accounts: ['alice@example.com', 'bob@example.com'].map((email, i) => ({
            email, status: 'OK', plan_type: 'plus', fetched_at: now,
            windows: {five_hour: {remaining_percent: 20 + i * 30}},
            reset_credits: [{
                id: `credit-${i}`, title: 'Full reset (Weekly + 5 hr)',
                expires_at: '2026-12-01T18:00:00Z', is_supported_by_plan: true,
            }],
        })),
        opencode_go: {
            status: 'OK — OpenCode Go', fetched_at: now,
            windows: {
                five_hour: {remaining_percent: 85},
                weekly: {remaining_percent: 60},
                monthly: {remaining_percent: 40},
            },
        },
    };
}

function firstReset(instance) {
    return instance._accountRows.get('alice@example.com').resetItems.get('credit-0');
}

test('render completes both accounts, all Go meters and the last-read date', () => {
    const instance = renderer();
    instance._render(report());
    assert.equal(instance._accountRows.size, 2);
    assert.match(instance._accountRows.get('bob@example.com').heading.label.text, /bob/);
    assert.equal(instance._goStatus.label.text, '● OpenCode Go actualizado');
    assert.deepEqual(instance._goDashboard.item.children.map(column => column.children[2].text),
        ['85%', '60%', '40%']);
    assert.doesNotMatch(instance._updated.label.text, /1970|· —/);
    assert.match(instance._updated.label.text, new RegExp(String(new Date().getFullYear()).slice(-2)));
});

test('reset buttons use the St actor API and do not stretch to the two-line row height', () => {
    const instance = renderer();
    instance._render(report());
    const row = firstReset(instance);
    assert.equal(row.button.label, 'Usar');
    assert.equal(row.button.style_class, 'chatgpt-usage-reset-button');
    assert.equal(row.button.y_align, 'center');
    assert.equal(row.button.y_expand, false);
    assert.equal(row.button.reactive, true);
    setResetButtonEnabled(row.button, false);
    assert.equal(row.button.reactive, false);
    assert.equal(row.button.can_focus, false);
    assert.ok(row.button.pseudoClasses.has('disabled'));
    setResetButtonEnabled(row.button, true);
    assert.ok(!row.button.pseudoClasses.has('disabled'));
});

test('the first click only arms confirmation, and closing the menu cancels it', () => {
    const instance = renderer();
    instance._render(report());
    const row = firstReset(instance);
    const previousCount = subprocessCount;
    instance._onResetButton(row);
    assert.equal(row.button.label, 'Confirmar');
    assert.equal(row.confirmed, true);
    assert.equal(subprocessCount, previousCount);
    instance._clearResetConfirmations();
    assert.equal(row.button.label, 'Usar');
    assert.equal(row.confirmed, false);
});

test('paused or incompatible accounts cannot arm reset confirmation', () => {
    const instance = renderer();
    const data = report();
    data.polling_enabled = false;
    instance._render(data);
    const row = firstReset(instance);
    instance._onResetButton(row);
    assert.equal(row.confirmed, false);
    assert.equal(row.button.reactive, false);

    data.polling_enabled = true;
    data.chatgpt_accounts[0].reset_credits[0].is_supported_by_plan = false;
    instance._render(data);
    instance._onResetButton(row);
    assert.equal(row.confirmed, false);
    assert.equal(row.button.reactive, false);
});

test('a reset UI failure never prevents the second account or Go from rendering', () => {
    const instance = renderer();
    instance._updateResetCredits = () => { throw new Error('Injected reset-control error'); };
    instance._render(report());
    assert.equal(instance._accountRows.size, 2);
    assert.equal(instance._goStatus.label.text, '● OpenCode Go actualizado');
    assert.equal(instance._goDashboard.item.children[0].children[2].text, '85%');
    assert.match(instance._status.label.text, /error al mostrar/);
});

test('new reset rows stay within their own account section', () => {
    const instance = renderer();
    const data = report();
    instance._render(data);
    data.chatgpt_accounts[0].reset_credits.push({
        ...data.chatgpt_accounts[0].reset_credits[0], id: 'new-credit',
    });
    instance._render(data);
    const alice = instance._accountRows.get('alice@example.com');
    const bob = instance._accountRows.get('bob@example.com');
    assert.ok(alice.section.children.includes(alice.resetItems.get('new-credit').item));
    assert.ok(!bob.section.children.includes(alice.resetItems.get('new-credit').item));
    data.chatgpt_accounts = [data.chatgpt_accounts[1]];
    instance._render(data);
    assert.equal(alice.section.destroyed, true);
    assert.equal(instance._accountRows.size, 1);
});
