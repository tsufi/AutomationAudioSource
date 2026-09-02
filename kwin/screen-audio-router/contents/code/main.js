const serviceName = "org.screenaudiorouter.Service";
const objectPath = "/org/screenaudiorouter/Service";
const interfaceName = "org.screenaudiorouter.KWin";
const outputByWindow = {};

function runtimeId(window) {
    return String(window.internalId);
}

function windowType(window) {
    if (window.normalWindow) return "normal";
    if (window.dialog) return "dialog";
    if (window.dock) return "dock";
    if (window.menu) return "menu";
    if (window.popupWindow) return "popup";
    if (window.splash) return "splash";
    if (window.utility) return "utility";
    return "other";
}

function facts(window) {
    return {
        runtimeId: runtimeId(window),
        resourceClass: window.resourceClass || null,
        resourceName: window.resourceName || null,
        caption: window.caption || null,
        pid: window.pid > 0 ? window.pid : null,
        outputName: window.output ? window.output.name : null,
        active: Boolean(window.active),
        normalWindow: Boolean(window.normalWindow),
        windowType: windowType(window)
    };
}

function report(eventName, payload) {
    callDBus(serviceName, objectPath, interfaceName, "ReportEvent", eventName, JSON.stringify(payload));
}

function reportUpdate(window) {
    report("updated", facts(window));
}

function watchWindow(window, reportAdded) {
    const id = runtimeId(window);
    outputByWindow[id] = window.output ? window.output.name : null;
    window.outputChanged.connect(function() {
        const current = window.output ? window.output.name : null;
        outputByWindow[id] = current;
        report("output_changed", facts(window));
    });
    window.captionChanged.connect(function() { reportUpdate(window); });
    window.windowClassChanged.connect(function() { reportUpdate(window); });
    if (reportAdded) report("added", facts(window));
}

const initialWindows = workspace.stackingOrder;
for (const window of initialWindows) watchWindow(window, false);
report("snapshot", initialWindows.map(facts));

workspace.windowAdded.connect(function(window) {
    watchWindow(window, true);
});

workspace.windowRemoved.connect(function(window) {
    const id = runtimeId(window);
    delete outputByWindow[id];
    report("removed", {runtimeId: id});
});

workspace.windowActivated.connect(function(window) {
    if (window) report("activated", facts(window));
});
