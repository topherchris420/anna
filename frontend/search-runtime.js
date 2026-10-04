/* Anna search runtime: the one client every product operation goes through.
 *
 * Two providers sit behind it. Live is Anna's research backend (/api/v1);
 * Demo is the bundled three-record corpus. The runtime never swaps one for
 * the other on its own: when Live is slow it says it is waking, when Live is
 * down it says it is unavailable, and Demo runs only after the user picks it.
 *
 * Phases (snapshot.phase):
 *   connecting    first health probe in flight
 *   waking        Live is slow or failing; probing again within a time budget
 *                 (a free-tier host sleeps when idle and takes a while to boot)
 *   live          Live answered and its index is ready
 *   unavailable   the budget ran out, or the endpoint is wrong; background
 *                 probes continue and restore Live when it answers
 *   reconnecting  a user-requested retry is in flight
 *   demo          the user chose Demo Mode
 *
 * Operations requested before Live is ready wait for it (queued, abortable)
 * and fail with an "unavailable" error if it never comes — they are never
 * answered from the Demo corpus.
 */
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
    var vector = body.vector_search === true;
    // Older backends do not report what their vectors are; "unknown" until a
    // search response says (retrieval.embedding), never assumed semantic.
    var embedding =
      provider === "demo"
        ? "none"
        : body.embedding === "hashing" || body.embedding === "sentence-transformer"
          ? body.embedding
          : "unknown";
    return Object.freeze({
      provider: provider,
      ready:
        body.ready === true ||
        (body.ready == null && body.index_exists === true),
      backend: String(body.backend || (provider === "demo" ? "bundled" : "?")),
      retrieval: String(
        body.retrieval || (provider === "demo" ? "demo-lexical" : "hybrid")
      ),
      vector_search: vector,
      embedding: embedding,
      semantic_search:
        vector &&
        (body.semantic_search === true ||
          (body.semantic_search == null && embedding === "sentence-transformer")),
      document_count: Math.max(0, Number(body.document_count) || 0),
      label:
        provider === "demo"
          ? "Demo · " +
            (Number(body.document_count) || 0) +
            " bundled documents"
          : "Live · " + String(body.backend || "backend"),
    });
  }

  /* A search response can tell us what the vectors were when /health could
     not (older backends). Returns updated capabilities, or the same object. */
  function learnFromSearch(capabilities, body) {
    var reported =
      body && body.retrieval && typeof body.retrieval.embedding === "string"
        ? body.retrieval.embedding
        : null;
    if (
      !capabilities ||
      capabilities.provider !== "live" ||
      capabilities.embedding !== "unknown" ||
      (reported !== "hashing" && reported !== "sentence-transformer")
    ) {
      return capabilities;
    }
    return Object.freeze(
      Object.assign({}, capabilities, {
        embedding: reported,
        semantic_search:
          capabilities.vector_search && reported === "sentence-transformer",
      })
    );
  }

  function invalid(what) {
    return new ProviderError(
      "invalid-response",
      "Backend returned an invalid " + what + " response"
    );
  }

  function validateSearchResponse(body) {
    if (
      !body ||
      !Array.isArray(body.hits) ||
      !Number.isFinite(Number(body.total)) ||
      !body.facets ||
      typeof body.facets !== "object"
    ) {
      throw invalid("search");
    }
    return body;
  }

  function validateSummaryResponse(body) {
    if (
      !body ||
      typeof body.answer !== "string" ||
      !Array.isArray(body.citations)
    ) {
      throw invalid("summary");
    }
    return body;
  }

  function validateVerifyResponse(body) {
    if (
      !body ||
      !body.fingerprint ||
      typeof body.fingerprint !== "object" ||
      !Array.isArray(body.excerpts) ||
      !body.counts ||
      typeof body.counts !== "object" ||
      typeof body.ok !== "boolean"
    ) {
      throw invalid("verification");
    }
    return body;
  }

  function validateSourcesResponse(body) {
    if (!body || !Array.isArray(body.sources)) throw invalid("source");
    return body;
  }

  function validateDocumentResponse(body) {
    if (!body || typeof body.id !== "string" || typeof body.title !== "string") {
      throw invalid("document");
    }
    return body;
  }

  function validateRelatedResponse(body) {
    if (!body || !Array.isArray(body.related)) throw invalid("related");
    return body;
  }

  function validateCompareResponse(body) {
    if (
      !body ||
      !body.a ||
      !body.b ||
      !Array.isArray(body.shared_terms) ||
      !Number.isFinite(Number(body.text_similarity))
    ) {
      throw invalid("comparison");
    }
    return body;
  }

  function validateCollectionsResponse(body) {
    if (!body || !Array.isArray(body.collections)) throw invalid("collections");
    return body;
  }

  function validateCollection(body) {
    if (!body || !Number.isFinite(Number(body.id)) || typeof body.name !== "string") {
      throw invalid("collection");
    }
    return body;
  }

  function validateBookmark(body) {
    if (!body || typeof body.document_id !== "string") throw invalid("bookmark");
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
    ["year_from", "year_to"].forEach(function (key) {
      var year = Number((request.filters || {})[key]);
      if (Number.isInteger(year) && year > 0) params.set(key, String(year));
    });
    return params;
  }

  function createLiveProvider(options) {
    var fetchImpl = options.fetchImpl || fetch;
    var getBaseUrl = options.getBaseUrl;
    var healthTimeoutMs = options.healthTimeoutMs || 20000;
    var requestTimeoutMs = options.requestTimeoutMs || 30000;

    function url(path) {
      return (
        String(getBaseUrl() || "").replace(/\/+$/, "") + "/api/v1" + path
      );
    }

    function fetchJSON(path, init, deadlineMs, outerSignal) {
      if (!getBaseUrl()) {
        return Promise.reject(
          new ProviderError(
            "not-configured",
            "No backend endpoint is configured (Edit ▸ API Endpoint…)"
          )
        );
      }
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

    function postJSON(path, body, signal) {
      return fetchJSON(
        path,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
        requestTimeoutMs,
        signal
      );
    }

    function ownerQuery(owner) {
      return "owner=" + encodeURIComponent(owner);
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
        return postJSON(
          "/summarize",
          { q: request.query, ids: request.documentIds || [] },
          signal
        ).then(validateSummaryResponse);
      },
      sources: function (signal) {
        return fetchJSON("/sources", {}, requestTimeoutMs, signal).then(
          validateSourcesResponse
        );
      },
      verify: function (payload, signal) {
        return postJSON("/evidence/verify", payload, signal).then(
          validateVerifyResponse
        );
      },
      document: function (id, signal) {
        return fetchJSON(
          "/document/" + encodeURIComponent(id),
          {},
          requestTimeoutMs,
          signal
        ).then(validateDocumentResponse);
      },
      related: function (id, signal) {
        return fetchJSON(
          "/document/" + encodeURIComponent(id) + "/related?size=6",
          {},
          requestTimeoutMs,
          signal
        ).then(validateRelatedResponse);
      },
      compare: function (a, b, signal) {
        return postJSON("/compare", { a: a, b: b }, signal).then(
          validateCompareResponse
        );
      },
      collections: function (owner, signal) {
        return fetchJSON(
          "/collections?" + ownerQuery(owner) + "&with_bookmarks=1",
          {},
          requestTimeoutMs,
          signal
        )
          .then(validateCollectionsResponse)
          .then(function (body) {
            // Backends older than ?with_bookmarks list counts only; fetch the
            // bookmarks the same way the server UI does, one collection each.
            var missing = body.collections.filter(function (c) {
              return !Array.isArray(c.bookmarks);
            });
            if (!missing.length) return body;
            return Promise.all(
              body.collections.map(function (c) {
                if (Array.isArray(c.bookmarks)) return c;
                return fetchJSON(
                  "/collections/" + Number(c.id) + "?" + ownerQuery(owner),
                  {},
                  requestTimeoutMs,
                  signal
                ).then(validateCollection);
              })
            ).then(function (collections) {
              return { owner: owner, collections: collections };
            });
          });
      },
      createCollection: function (owner, name, signal) {
        return postJSON(
          "/collections",
          { owner: owner, name: name },
          signal
        ).then(validateCollection);
      },
      deleteCollection: function (owner, id, signal) {
        return fetchJSON(
          "/collections/" + Number(id) + "?" + ownerQuery(owner),
          { method: "DELETE" },
          requestTimeoutMs,
          signal
        );
      },
      addBookmark: function (owner, collectionId, doc, signal) {
        return postJSON(
          "/collections/" + Number(collectionId) + "/bookmarks",
          {
            owner: owner,
            document_id: doc.id,
            title: doc.title || "",
            url: doc.url || "",
            source: doc.source || "",
          },
          signal
        ).then(validateBookmark);
      },
      removeBookmark: function (owner, collectionId, documentId, signal) {
        return fetchJSON(
          "/collections/" +
            Number(collectionId) +
            "/bookmarks/" +
            encodeURIComponent(documentId) +
            "?" +
            ownerQuery(owner),
          { method: "DELETE" },
          requestTimeoutMs,
          signal
        );
      },
    };
  }

  function availabilityError(error) {
    return (
      !!error &&
      error.name !== "AbortError" &&
      ["timeout", "offline", "unavailable", "invalid-response"].indexOf(
        error.code
      ) >= 0
    );
  }

  /* Plain-language reason for a failed probe, for the status line. */
  function describeFailure(error) {
    if (!error) return "Anna's research backend is unavailable";
    switch (error.code) {
      case "timeout":
        return "Anna's research backend did not answer in time";
      case "offline":
        return "This browser is offline";
      case "invalid-response":
        return "The configured endpoint did not answer like Anna's API";
      case "not-configured":
        return error.message;
      case "not-ready":
        return "Anna's research index is not ready yet";
      case "http-client":
        return (
          "The configured endpoint refused the request (" +
          (error.status ? "HTTP " + error.status + ": " : "") +
          error.message +
          ")"
        );
      default:
        return "Anna's research backend isn't reachable";
    }
  }

  function createRuntime(options) {
    var live = options.liveProvider;
    var demo = options.demoProvider;
    var retryDelays = options.retryDelays || [15000, 30000, 60000];
    var wakeBudgetMs = options.wakeBudgetMs == null ? 150000 : options.wakeBudgetMs;
    var wakeRetryMs = options.wakeRetryMs == null ? 3000 : options.wakeRetryMs;
    var slowAfterMs = options.slowAfterMs == null ? 2500 : options.slowAfterMs;
    var now = options.now || Date.now;

    var listeners = [];
    var waiters = [];
    var retryIndex = 0;
    var retryTimer = null;
    var stopped = false;
    var liveCapabilities = null;
    var demoCapabilities = null;
    var lifecycleGeneration = 0;
    var searchGeneration = 0;
    var summaryGeneration = 0;
    var cycle = null;
    var controllers = {};
    var detached = [];
    var snapshot = Object.freeze({
      phase: "connecting",
      provider: "live",
      capabilities: null,
      liveAvailable: false,
      reason: "",
      wakeStartedAt: null,
      nextRetryAt: null,
      lastProbe: null,
    });

    function publish(patch) {
      snapshot = Object.freeze(Object.assign({}, snapshot, patch));
      waiters.slice().forEach(function (waiter) {
        waiter(snapshot);
      });
      listeners.slice().forEach(function (listener) {
        listener(snapshot);
      });
      return snapshot;
    }

    function controllerFor(key) {
      if (key == null) {
        var own = new AbortController();
        detached.push(own);
        return own;
      }
      if (controllers[key]) controllers[key].abort();
      controllers[key] = new AbortController();
      return controllers[key];
    }

    function clearController(key, controller) {
      if (key == null) {
        detached = detached.filter(function (c) {
          return c !== controller;
        });
      } else if (controllers[key] === controller) {
        controllers[key] = null;
      }
    }

    function isActive(generation) {
      return !stopped && generation === lifecycleGeneration;
    }

    function clearRetry() {
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = null;
    }

    /* Background probes after a failed cycle (or while Demo is selected),
       so Live is restored — or offered — as soon as it answers. */
    function scheduleReconnect() {
      if (stopped || !retryDelays.length || retryTimer) return;
      var delay = retryDelays[Math.min(retryIndex, retryDelays.length - 1)];
      retryIndex += 1;
      publish({ nextRetryAt: now() + delay });
      retryTimer = setTimeout(function () {
        retryTimer = null;
        if (
          (typeof navigator !== "undefined" && navigator.onLine === false) ||
          (typeof document !== "undefined" && document.hidden)
        ) {
          scheduleReconnect();
          return;
        }
        probe("background").catch(function () {});
      }, delay);
    }

    function demoSnapshot(patch) {
      return Object.assign(
        {
          phase: "demo",
          provider: "demo",
          capabilities: demoCapabilities,
          wakeStartedAt: null,
        },
        patch
      );
    }

    /* One probe cycle: health attempts until Live is ready, the wake budget
       runs out, or the endpoint answers with a client error. Concurrent
       callers share the cycle in flight.

       kind: "start"      page load
             "retry"      the user asked (Retry, a search while unavailable)
             "recheck"    a Live request failed for availability
             "background" scheduled reconnect: one quiet attempt */
    function probe(kind) {
      if (stopped) return Promise.reject(abortError());
      if (cycle) {
        if (kind !== "background" && cycle.kind === "background") {
          cycle.kind = kind; // a user is now waiting on it: report progress
          if (snapshot.provider === "live") {
            publish({ phase: "reconnecting", wakeStartedAt: cycle.startedAt });
          }
        }
        return cycle.promise;
      }
      clearRetry();
      var lifecycle = lifecycleGeneration;
      var current = { kind: kind, startedAt: now(), timer: null, slow: null };
      cycle = current;
      current.promise = new Promise(function (resolve, reject) {
        current.resolve = resolve;
        current.reject = reject;
      });

      if (snapshot.provider === "live" && kind !== "background") {
        publish({
          phase: kind === "start" ? "connecting" : "reconnecting",
          reason: "",
          wakeStartedAt: current.startedAt,
          nextRetryAt: null,
        });
      } else if (snapshot.provider === "demo" && kind === "retry") {
        publish({ phase: "reconnecting", nextRetryAt: null });
      }

      function finish(patch) {
        if (current.slow) clearTimeout(current.slow);
        if (current.timer) clearTimeout(current.timer);
        if (cycle === current) cycle = null;
        current.resolve(publish(patch));
      }

      function succeed(capabilities) {
        liveCapabilities = capabilities;
        retryIndex = 0;
        clearRetry();
        if (snapshot.provider === "demo") {
          finish(
            demoSnapshot({
              liveAvailable: true,
              reason: "Anna is online",
              nextRetryAt: null,
            })
          );
        } else {
          finish({
            phase: "live",
            provider: "live",
            capabilities: capabilities,
            liveAvailable: true,
            reason: "",
            wakeStartedAt: null,
            nextRetryAt: null,
          });
        }
      }

      function fail(error) {
        var reason = describeFailure(error);
        if (snapshot.provider === "demo") {
          finish(demoSnapshot({ liveAvailable: false, reason: reason }));
        } else {
          finish({
            phase: "unavailable",
            provider: "live",
            capabilities: liveCapabilities,
            liveAvailable: false,
            reason: reason,
            wakeStartedAt: null,
          });
        }
        scheduleReconnect();
      }

      function attempt() {
        if (!isActive(lifecycle) || cycle !== current) return;
        var controller = controllerFor("health");
        var started = now();
        if (snapshot.provider === "live" && current.kind !== "background") {
          // A cold host holds the request open while it boots; say so.
          current.slow = setTimeout(function () {
            if (isActive(lifecycle) && cycle === current) {
              publish({ phase: "waking" });
            }
          }, slowAfterMs);
        }
        live
          .health(controller.signal)
          .then(
            function (body) {
              if (!isActive(lifecycle) || cycle !== current) return;
              // Idempotent: providers may hand back raw /health bodies.
              var capabilities = normalizeCapabilities(body, "live");
              publish({
                lastProbe: {
                  at: started,
                  ms: now() - started,
                  ok: true,
                  ready: capabilities.ready,
                  code: capabilities.ready ? "ok" : "not-ready",
                  status: 200,
                  message: capabilities.ready
                    ? capabilities.document_count + " documents"
                    : "index not ready",
                },
              });
              if (capabilities.ready) return succeed(capabilities);
              retryOrFail(
                new ProviderError("not-ready", "Index not ready", 200)
              );
            },
            function (error) {
              if (!isActive(lifecycle) || cycle !== current) return;
              if (error && error.name === "AbortError") return;
              publish({
                lastProbe: {
                  at: started,
                  ms: now() - started,
                  ok: false,
                  ready: false,
                  code: (error && error.code) || "unavailable",
                  status: (error && error.status) || 0,
                  message: (error && error.message) || "",
                },
              });
              if (availabilityError(error)) return retryOrFail(error);
              fail(error);
            }
          )
          .finally(function () {
            if (current.slow) clearTimeout(current.slow);
            current.slow = null;
            clearController("health", controller);
          });
      }

      function retryOrFail(error) {
        var elapsed = now() - current.startedAt;
        if (current.kind === "background" || elapsed + wakeRetryMs >= wakeBudgetMs) {
          return fail(error);
        }
        if (snapshot.provider === "live") {
          publish({ phase: "waking", reason: describeFailure(error) });
        }
        current.timer = setTimeout(attempt, wakeRetryMs);
      }

      attempt();
      return current.promise;
    }

    /* Abandon the probe cycle in flight. Its callers are settled: with the
       current snapshot when the user changed course, or with an AbortError
       when the runtime is stopping. */
    function cancelCycle(aborting) {
      if (!cycle) return;
      var cancelled = cycle;
      cycle = null;
      if (cancelled.timer) clearTimeout(cancelled.timer);
      if (cancelled.slow) clearTimeout(cancelled.slow);
      if (controllers.health) controllers.health.abort();
      if (aborting) cancelled.reject(abortError());
      else cancelled.resolve(snapshot);
    }

    function start() {
      stopped = false;
      return probe("start");
    }

    function retryLive() {
      if (stopped) return Promise.reject(abortError());
      return probe("retry");
    }

    /* Strict switch: only when Live is known to be ready. */
    function switchToLive() {
      if (!liveCapabilities || !liveCapabilities.ready) {
        throw new ProviderError("unavailable", "Live backend is not ready");
      }
      clearRetry();
      publish({
        phase: "live",
        provider: "live",
        capabilities: liveCapabilities,
        liveAvailable: true,
        reason: "",
        wakeStartedAt: null,
        nextRetryAt: null,
      });
    }

    /* The user wants the real system: switch now if Live is ready, otherwise
       go through a visible wake cycle. Never falls back to Demo. ``fresh``
       forgets what is known about Live (the endpoint just changed). */
    function useLive(options) {
      if (stopped) return Promise.reject(abortError());
      if (options && options.fresh) {
        liveCapabilities = null;
        publish({ liveAvailable: false });
      }
      if (snapshot.liveAvailable && liveCapabilities && liveCapabilities.ready) {
        switchToLive();
        return Promise.resolve(snapshot);
      }
      cancelCycle();
      clearRetry();
      publish({
        provider: "live",
        phase: "reconnecting",
        capabilities: liveCapabilities,
        reason: "",
      });
      return probe("retry");
    }

    function useDemo() {
      var lifecycle = lifecycleGeneration;
      if (!isActive(lifecycle)) return Promise.reject(abortError());
      var liveWasReady = snapshot.phase === "live";
      return demo.health().then(function (capabilities) {
        if (!isActive(lifecycle)) throw abortError();
        demoCapabilities = normalizeCapabilities(capabilities, "demo");
        cancelCycle();
        clearRetry();
        publish(
          demoSnapshot({
            liveAvailable: liveWasReady,
            reason: "Demo selected",
            nextRetryAt: null,
          })
        );
        if (!liveWasReady) scheduleReconnect();
        return snapshot;
      });
    }

    /* Resolves with the provider an operation should use once one is
       usable: Demo when selected, Live when ready. Waits through connecting
       and waking; rejects when Live becomes unavailable or the wait is
       aborted. A request made while unavailable starts a retry — searching
       is itself a way to ask for Anna again. */
    function whenReady(signal) {
      if (snapshot.provider === "demo") return Promise.resolve(demo);
      if (snapshot.phase === "live") return Promise.resolve(live);
      if (snapshot.phase === "unavailable") probe("retry").catch(function () {});
      return new Promise(function (resolve, reject) {
        function done() {
          waiters = waiters.filter(function (w) {
            return w !== waiter;
          });
          if (signal) signal.removeEventListener("abort", onAbort);
        }
        function onAbort() {
          done();
          reject(abortError());
        }
        function waiter(next) {
          if (next.provider === "demo") {
            done();
            reject(abortError()); // the user switched modes; caller re-runs
          } else if (next.phase === "live") {
            done();
            resolve(live);
          } else if (next.phase === "unavailable") {
            done();
            reject(new ProviderError("unavailable", next.reason));
          }
        }
        if (signal) {
          if (signal.aborted) return reject(abortError());
          signal.addEventListener("abort", onAbort, { once: true });
        }
        waiters.push(waiter);
      });
    }

    /* Run one provider operation. ``key`` names the slot whose previous
       request a new one supersedes; null gives the request its own slot
       (writes must not cancel each other). */
    function run(key, invoke, isCurrent) {
      var lifecycle = lifecycleGeneration;
      var controller = controllerFor(key);
      var selected = null;
      return whenReady(controller.signal)
        .then(function (provider) {
          if (!isActive(lifecycle)) throw abortError();
          selected = provider;
          return invoke(provider, controller.signal);
        })
        .then(
          function (result) {
            if (!isActive(lifecycle) || (isCurrent && !isCurrent())) {
              throw abortError();
            }
            return result;
          },
          function (error) {
            if (!isActive(lifecycle) || (isCurrent && !isCurrent())) {
              throw abortError();
            }
            if (
              selected === live &&
              availabilityError(error) &&
              snapshot.provider === "live"
            ) {
              // Re-establish Live in the open; the caller reports this
              // request's failure. Nothing is retried through Demo.
              probe("recheck").catch(function () {});
            }
            throw error;
          }
        )
        .finally(function () {
          clearController(key, controller);
        });
    }

    function search(request) {
      searchGeneration += 1;
      summaryGeneration += 1;
      var generation = searchGeneration;
      if (controllers.summary) controllers.summary.abort();
      return run(
        "search",
        function (provider, signal) {
          return provider.search(request, signal);
        },
        function () {
          return generation === searchGeneration;
        }
      ).then(function (result) {
        if (snapshot.provider === "live" && snapshot.capabilities) {
          var learned = learnFromSearch(snapshot.capabilities, result);
          if (learned !== snapshot.capabilities) {
            liveCapabilities = learned;
            publish({ capabilities: learned });
          }
        }
        return result;
      });
    }

    function summarize(request) {
      summaryGeneration += 1;
      var generation = summaryGeneration;
      return run(
        "summary",
        function (provider, signal) {
          return provider.summarize(request, signal);
        },
        function () {
          return generation === summaryGeneration;
        }
      );
    }

    function sources() {
      return run("sources", function (provider, signal) {
        return provider.sources(signal);
      });
    }

    /* Verification goes to whichever provider is selected: Live re-reads
       excerpts from the backend index, Demo from the bundled corpus. A
       verdict from the wrong corpus would be worse than an error. */
    function verify(payload) {
      return run("verify", function (provider, signal) {
        return provider.verify(payload, signal);
      });
    }

    /* A search that is not the user's search (counts for a dialog): its own
       slot, so it never supersedes or is superseded by the results page. */
    function browse(request) {
      return run("browse", function (provider, signal) {
        return provider.search(request, signal);
      });
    }

    function documentDetail(id) {
      return run("document", function (provider, signal) {
        return provider.document(id, signal);
      });
    }

    function related(id) {
      return run("related", function (provider, signal) {
        return provider.related(id, signal);
      });
    }

    function compare(a, b) {
      return run("compare", function (provider, signal) {
        return provider.compare(a, b, signal);
      });
    }

    function collectionsOp(key, method, args) {
      return run(key, function (provider, signal) {
        if (typeof provider[method] !== "function") {
          throw new ProviderError(
            "demo-unsupported",
            "Collections are saved by Anna's research backend; Demo Mode has none."
          );
        }
        return provider[method].apply(provider, args.concat([signal]));
      });
    }

    function stop() {
      stopped = true;
      lifecycleGeneration += 1;
      searchGeneration += 1;
      summaryGeneration += 1;
      clearRetry();
      cancelCycle(true);
      Object.keys(controllers).forEach(function (key) {
        if (controllers[key]) controllers[key].abort();
        controllers[key] = null;
      });
      detached.forEach(function (c) {
        c.abort();
      });
      detached = [];
      waiters.slice().forEach(function (waiter) {
        waiter({ provider: "demo" }); // releases every queued operation
      });
      waiters = [];
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
      useLive: useLive,
      useDemo: useDemo,
      search: search,
      browse: browse,
      summarize: summarize,
      sources: sources,
      verify: verify,
      document: documentDetail,
      related: related,
      compare: compare,
      collections: function (owner) {
        return collectionsOp("collections", "collections", [owner]);
      },
      createCollection: function (owner, name) {
        return collectionsOp(null, "createCollection", [owner, name]);
      },
      deleteCollection: function (owner, id) {
        return collectionsOp(null, "deleteCollection", [owner, id]);
      },
      addBookmark: function (owner, collectionId, doc) {
        return collectionsOp(null, "addBookmark", [owner, collectionId, doc]);
      },
      removeBookmark: function (owner, collectionId, documentId) {
        return collectionsOp(null, "removeBookmark", [
          owner,
          collectionId,
          documentId,
        ]);
      },
    };
  }

  return {
    ProviderError: ProviderError,
    createLiveProvider: createLiveProvider,
    createRuntime: createRuntime,
    normalizeCapabilities: normalizeCapabilities,
    learnFromSearch: learnFromSearch,
    describeFailure: describeFailure,
    availabilityError: availabilityError,
    validateSearchResponse: validateSearchResponse,
    validateSummaryResponse: validateSummaryResponse,
    validateSourcesResponse: validateSourcesResponse,
    validateVerifyResponse: validateVerifyResponse,
    validateDocumentResponse: validateDocumentResponse,
    validateCompareResponse: validateCompareResponse,
    toSearchParams: toSearchParams,
  };
});
