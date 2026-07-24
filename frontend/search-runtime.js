(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.EngineSearchRuntime = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function ProviderError(code, message, status) {
    this.name = "ProviderError";
    this.code = code;
    this.message = message;
    this.status = status || 0;
  }
  ProviderError.prototype = Object.create(Error.prototype);

  function abortError() {
    var error = new Error("Request superseded");
    error.name = "AbortError";
    return error;
  }

  function normalizeCapabilities(body, provider) {
    body = body || {};
    return Object.freeze({
      provider: provider,
      ready:
        body.ready === true ||
        (body.ready == null && body.index_exists === true),
      backend: String(body.backend || (provider === "demo" ? "bundled" : "?")),
      retrieval: String(
        body.retrieval || (provider === "demo" ? "demo-lexical" : "hybrid")
      ),
      vector_search: body.vector_search === true,
      document_count: Math.max(0, Number(body.document_count) || 0),
      label:
        provider === "demo"
          ? "Demo \u00b7 " +
            (Number(body.document_count) || 0) +
            " bundled documents"
          : "Live \u00b7 " + String(body.backend || "backend"),
    });
  }

  function validateSearchResponse(body) {
    if (
      !body ||
      !Array.isArray(body.hits) ||
      !Number.isFinite(Number(body.total)) ||
      !body.facets ||
      typeof body.facets !== "object"
    ) {
      throw new ProviderError(
        "invalid-response",
        "Backend returned an invalid search response"
      );
    }
    return body;
  }

  function validateSummaryResponse(body) {
    if (
      !body ||
      typeof body.answer !== "string" ||
      !Array.isArray(body.citations)
    ) {
      throw new ProviderError(
        "invalid-response",
        "Backend returned an invalid summary response"
      );
    }
    return body;
  }

  function validateSourcesResponse(body) {
    if (!body || !Array.isArray(body.sources)) {
      throw new ProviderError(
        "invalid-response",
        "Backend returned an invalid source response"
      );
    }
    return body;
  }

  function toSearchParams(request) {
    var params = new URLSearchParams();
    params.set("q", request.q || "");
    params.set("mode", request.mode || "hybrid");
    params.set("page", String(request.page || 1));
    params.set("per_page", String(request.per_page || 20));
    ["source", "kind", "category", "language"].forEach(function (key) {
      ((request.filters || {})[key] || []).forEach(function (value) {
        params.append(key, value);
      });
    });
    ["has_code", "has_equations"].forEach(function (key) {
      if ((request.filters || {})[key] === "true") params.set(key, "true");
    });
    return params;
  }

  function createLiveProvider(options) {
    var fetchImpl = options.fetchImpl || fetch;
    var getBaseUrl = options.getBaseUrl;
    var healthTimeoutMs = options.healthTimeoutMs || 4000;
    var requestTimeoutMs = options.requestTimeoutMs || 15000;

    function url(path) {
      return (
        String(getBaseUrl() || "").replace(/\/+$/, "") + "/api/v1" + path
      );
    }

    function fetchJSON(path, init, deadlineMs, outerSignal) {
      var controller = new AbortController();
      var timedOut = false;
      var forwardAbort = function () {
        controller.abort();
      };
      if (outerSignal) {
        if (outerSignal.aborted) controller.abort();
        else outerSignal.addEventListener("abort", forwardAbort, { once: true });
      }
      var timer = setTimeout(function () {
        timedOut = true;
        controller.abort();
      }, deadlineMs);
      init = Object.assign({}, init || {}, { signal: controller.signal });
      return fetchImpl(url(path), init)
        .then(function (response) {
          return response
            .json()
            .catch(function () {
              if (!response.ok) {
                throw new ProviderError(
                  response.status >= 500 ? "unavailable" : "http-client",
                  response.statusText || "HTTP " + response.status,
                  response.status
                );
              }
              throw new ProviderError(
                "invalid-response",
                "Backend returned invalid JSON",
                response.status
              );
            })
            .then(function (body) {
              if (!response.ok) {
                throw new ProviderError(
                  response.status >= 500 ? "unavailable" : "http-client",
                  (body && typeof body.error === "string" && body.error) ||
                    response.statusText ||
                    "HTTP " + response.status,
                  response.status
                );
              }
              return body;
            });
        })
        .catch(function (error) {
          if (timedOut)
            throw new ProviderError("timeout", "Backend timed out");
          if (outerSignal && outerSignal.aborted) throw abortError();
          if (error instanceof ProviderError) throw error;
          throw new ProviderError(
            typeof navigator !== "undefined" && navigator.onLine === false
              ? "offline"
              : "unavailable",
            error.message || "Backend unavailable"
          );
        })
        .finally(function () {
          clearTimeout(timer);
          if (outerSignal) {
            outerSignal.removeEventListener("abort", forwardAbort);
          }
        });
    }

    return {
      health: function (signal) {
        return fetchJSON("/health", {}, healthTimeoutMs, signal).then(
          function (body) {
            return normalizeCapabilities(body, "live");
          }
        );
      },
      search: function (request, signal) {
        return fetchJSON(
          "/search?" + toSearchParams(request).toString(),
          {},
          requestTimeoutMs,
          signal
        ).then(validateSearchResponse);
      },
      summarize: function (request, signal) {
        return fetchJSON(
          "/summarize",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              q: request.query,
              ids: request.documentIds || [],
            }),
          },
          requestTimeoutMs,
          signal
        ).then(validateSummaryResponse);
      },
      sources: function (signal) {
        return fetchJSON("/sources", {}, requestTimeoutMs, signal).then(
          validateSourcesResponse
        );
      },
    };
  }

  function createRuntime(options) {
    var live = options.liveProvider;
    var demo = options.demoProvider;
    var retryDelays = options.retryDelays || [10000, 30000, 60000];
    var listeners = [];
    var retryIndex = 0;
    var retryTimer = null;
    var stopped = false;
    var liveCapabilities = null;
    var lifecycleGeneration = 0;
    var searchGeneration = 0;
    var summaryGeneration = 0;
    var controllers = {
      health: null,
      search: null,
      summary: null,
      sources: null,
    };
    var snapshot = {
      phase: "connecting",
      provider: "demo",
      capabilities: null,
      liveAvailable: false,
      reason: "",
    };
    var lastStableSnapshot = null;

    function publish(patch) {
      snapshot = Object.freeze(Object.assign({}, snapshot, patch));
      if (snapshot.phase === "live" || snapshot.phase === "demo") {
        lastStableSnapshot = snapshot;
      }
      listeners.slice().forEach(function (listener) {
        listener(snapshot);
      });
      return snapshot;
    }

    function controllerFor(key) {
      if (controllers[key]) controllers[key].abort();
      controllers[key] = new AbortController();
      return controllers[key];
    }

    function clearController(key, controller) {
      if (controllers[key] === controller) controllers[key] = null;
    }

    function isActive(generation) {
      return !stopped && generation === lifecycleGeneration;
    }

    function availabilityError(error) {
      return (
        error &&
        error.name !== "AbortError" &&
        ["timeout", "offline", "unavailable", "invalid-response"].indexOf(
          error.code
        ) >= 0
      );
    }

    function scheduleReconnect() {
      if (stopped || !retryDelays.length || retryTimer) return;
      var delay = retryDelays[Math.min(retryIndex, retryDelays.length - 1)];
      retryIndex += 1;
      retryTimer = setTimeout(function () {
        retryTimer = null;
        if (
          (typeof navigator !== "undefined" && navigator.onLine === false) ||
          (typeof document !== "undefined" && document.hidden)
        ) {
          scheduleReconnect();
          return;
        }
        retryLive().catch(function () {});
      }, delay);
    }

    function enterDemo(reason, isCurrent) {
      return demo.health().then(function (capabilities) {
        if (stopped || (isCurrent && !isCurrent())) throw abortError();
        publish({
          phase: "demo",
          provider: "demo",
          capabilities: normalizeCapabilities(capabilities, "demo"),
          liveAvailable: false,
          reason: reason || "Backend unavailable",
        });
        scheduleReconnect();
        return snapshot;
      });
    }

    function start() {
      stopped = false;
      var lifecycle = lifecycleGeneration;
      publish({ phase: "connecting", reason: "" });
      var controller = controllerFor("health");
      return live
        .health(controller.signal)
        .then(function (capabilities) {
          if (!isActive(lifecycle)) throw abortError();
          liveCapabilities = normalizeCapabilities(capabilities, "live");
          if (!liveCapabilities.ready) {
            return enterDemo("Backend is not ready", function () {
              return isActive(lifecycle);
            });
          }
          retryIndex = 0;
          return publish({
            phase: "live",
            provider: "live",
            capabilities: liveCapabilities,
            liveAvailable: true,
            reason: "",
          });
        })
        .catch(function (error) {
          if (error.name === "AbortError") throw error;
          if (!isActive(lifecycle)) throw abortError();
          if (!availabilityError(error)) {
            publish({
              phase: "connecting",
              provider: "demo",
              capabilities: null,
              liveAvailable: false,
              reason: error.message || error.code || "Live startup failed",
            });
            throw error;
          }
          return enterDemo(error.code || "unavailable", function () {
            return isActive(lifecycle);
          });
        })
        .finally(function () {
          clearController("health", controller);
        });
    }

    function retryLive() {
      var lifecycle = lifecycleGeneration;
      if (!isActive(lifecycle)) return Promise.reject(abortError());
      var stableSnapshot = lastStableSnapshot;
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = null;
      publish({ phase: "reconnecting" });
      var controller = controllerFor("health");
      return live
        .health(controller.signal)
        .then(function (capabilities) {
          if (!isActive(lifecycle)) throw abortError();
          liveCapabilities = normalizeCapabilities(capabilities, "live");
          retryIndex = 0;
          if (snapshot.provider === "demo") {
            publish({
              phase: "demo",
              liveAvailable: liveCapabilities.ready,
              reason: liveCapabilities.ready
                ? "Full index available"
                : "Backend is not ready",
            });
          } else {
            publish({
              phase: "live",
              provider: "live",
              capabilities: liveCapabilities,
              liveAvailable: liveCapabilities.ready,
              reason: "",
            });
          }
          return snapshot;
        })
        .catch(function (error) {
          if (error.name === "AbortError") throw error;
          if (!isActive(lifecycle)) throw abortError();
          if (!availabilityError(error)) {
            var rollbackSnapshot = stableSnapshot || {
              phase: "connecting",
              provider: "demo",
              capabilities: null,
              liveAvailable: false,
            };
            publish({
              phase: rollbackSnapshot.phase,
              provider: rollbackSnapshot.provider,
              capabilities: rollbackSnapshot.capabilities,
              liveAvailable: rollbackSnapshot.liveAvailable,
              reason: error.message || error.code || "Live retry failed",
            });
            throw error;
          }
          return enterDemo(error.code || "unavailable", function () {
            return isActive(lifecycle);
          });
        })
        .finally(function () {
          clearController("health", controller);
        });
    }

    function switchToLive() {
      if (!liveCapabilities || !liveCapabilities.ready) {
        throw new ProviderError("unavailable", "Live backend is not ready");
      }
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = null;
      publish({
        phase: "live",
        provider: "live",
        capabilities: liveCapabilities,
        liveAvailable: true,
        reason: "",
      });
    }

    function useDemo(reason) {
      var lifecycle = lifecycleGeneration;
      if (!isActive(lifecycle)) return Promise.reject(abortError());
      return enterDemo(reason || "Demo selected", function () {
        return isActive(lifecycle);
      });
    }

    function search(request) {
      searchGeneration += 1;
      summaryGeneration += 1;
      var generation = searchGeneration;
      var lifecycle = lifecycleGeneration;
      var controller = controllerFor("search");
      if (controllers.summary) controllers.summary.abort();
      var selected = snapshot.provider === "live" ? live : demo;
      return selected
        .search(request, controller.signal)
        .catch(function (error) {
          if (!isActive(lifecycle) || generation !== searchGeneration)
            throw abortError();
          if (
            snapshot.provider !== "live" ||
            !availabilityError(error)
          )
            throw error;
          return enterDemo(error.code, function () {
            return (
              isActive(lifecycle) && generation === searchGeneration
            );
          }).then(function () {
            if (!isActive(lifecycle) || generation !== searchGeneration)
              throw abortError();
            return demo.search(request, controller.signal);
          });
        })
        .then(function (result) {
          if (!isActive(lifecycle) || generation !== searchGeneration)
            throw abortError();
          return result;
        })
        .finally(function () {
          clearController("search", controller);
        });
    }

    function summarize(request) {
      summaryGeneration += 1;
      var generation = summaryGeneration;
      var lifecycle = lifecycleGeneration;
      var controller = controllerFor("summary");
      var selected = snapshot.provider === "live" ? live : demo;
      return selected
        .summarize(request, controller.signal)
        .then(function (result) {
          if (!isActive(lifecycle) || generation !== summaryGeneration)
            throw abortError();
          return result;
        })
        .finally(function () {
          clearController("summary", controller);
        });
    }

    function sources() {
      var lifecycle = lifecycleGeneration;
      var controller = controllerFor("sources");
      var selected = snapshot.provider === "live" ? live : demo;
      return selected
        .sources(controller.signal)
        .then(function (result) {
          if (!isActive(lifecycle)) throw abortError();
          return result;
        })
        .finally(function () {
          clearController("sources", controller);
        });
    }

    function stop() {
      stopped = true;
      lifecycleGeneration += 1;
      searchGeneration += 1;
      summaryGeneration += 1;
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = null;
      Object.keys(controllers).forEach(function (key) {
        if (controllers[key]) controllers[key].abort();
        controllers[key] = null;
      });
    }

    return {
      getSnapshot: function () {
        return snapshot;
      },
      subscribe: function (listener) {
        listeners.push(listener);
        listener(snapshot);
        return function () {
          listeners = listeners.filter(function (item) {
            return item !== listener;
          });
        };
      },
      start: start,
      stop: stop,
      retryLive: retryLive,
      switchToLive: switchToLive,
      useDemo: useDemo,
      search: search,
      summarize: summarize,
      sources: sources,
    };
  }

  return {
    ProviderError: ProviderError,
    createLiveProvider: createLiveProvider,
    createRuntime: createRuntime,
    normalizeCapabilities: normalizeCapabilities,
    validateSearchResponse: validateSearchResponse,
    validateSummaryResponse: validateSummaryResponse,
    validateSourcesResponse: validateSourcesResponse,
    toSearchParams: toSearchParams,
  };
});
