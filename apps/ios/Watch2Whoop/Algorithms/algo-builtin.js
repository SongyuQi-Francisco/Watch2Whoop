// Placeholder of the built-in algorithm bundle (#340 N0). It only proves the calling convention of
// apps/ios/Watch2Whoop/Algorithms/AlgorithmRuntime.swift (api 1). N1 replaces this file with the real bundle built
// from packages/openbodyscore (same functions on the server, golden tests in both languages).
(function (g) {
  "use strict";
  var fns = {
    // Returns its input: tests check that JSON goes through unchanged.
    echo: function (input) { return input; },
    // The bundle's identity, for the data status page.
    about: function () { return { version: g.OBS.version, api: g.OBS.apiVersion, placeholder: true }; }
  };
  g.OBS = {
    apiVersion: 1,
    version: "0.0.0-n0",
    functions: Object.keys(fns),
    call: function (name, inputJson) {
      var fn = fns[name];
      if (!fn) return JSON.stringify({ error: { code: "unknown_function", message: String(name) } });
      try {
        return JSON.stringify(fn(JSON.parse(inputJson)));
      } catch (e) {
        return JSON.stringify({ error: { code: "exception", message: String(e && e.message || e) } });
      }
    }
  };
})(globalThis);
