/* Shared page script for the Screwhead Networks site.
 *
 * Two jobs: keep the status bar clock ticking, and, on pages that embed an
 * <applet>, boot the CheerpJ runtime and report progress in the status strip.
 */
(function () {
    "use strict";

    /* ---- status bar clock ---- */

    function pad(n) {
        return (n < 10 ? "0" : "") + n;
    }

    var clock = document.getElementById("status-clock");

    if (clock) {
        (function tick() {
            var d = new Date();
            clock.textContent = pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
            setTimeout(tick, 1000);
        }());
    }

    /* ---- login form ----------------------------------------------------
     *
     * The fields are decoration and are deliberately not checked against
     * anything. A credential check that runs in the visitor's browser is not a
     * check: the code and any password in it are downloaded by whoever asks.
     *
     * Access to the admin panel is enforced by the web server, which requires
     * a client certificate signed by the Screwhead Networks root CA. See
     * deploy/nginx-mtls.conf. Without that certificate the server answers 403
     * regardless of what is typed here, and with it the panel is reachable
     * whether or not this page was ever visited.
     * -------------------------------------------------------------------- */

    var loginForm = document.getElementById("loginform");

    if (loginForm) {
        loginForm.addEventListener("submit", function (event) {
            event.preventDefault();

            var bar = document.getElementById("status-msg");

            function say(message) {
                if (bar) {
                    bar.textContent = message;
                }
            }

            /* Deliberately the same message for every kind of failure --
               rejected certificate, rate limited, backend down, network error.
               Telling someone which one it was tells them what to try next. */
            function refuse() {
                say("Login incorrect.");
                document.getElementById("password").value = "";
                document.getElementById("username").focus();
            }

            say("Authenticating...");

            fetch("login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username: document.getElementById("username").value,
                    password: document.getElementById("password").value
                })
            }).then(function (response) {
                if (response.ok) {
                    window.location.href = "admin-panel/index.html";
                    return;
                }
                refuse();
            }).catch(refuse);
        });
    }

    /* ---- CheerpJ, only on pages that actually embed an applet ---- */

    var applet = document.querySelector("applet");

    if (!applet) {
        return;
    }

    /* Read this now: CheerpJ swaps the <applet> element out for its own display
       during init, so the tag is gone by the time the promise resolves. */
    var appletCode = applet.getAttribute("code");

    var lamp    = document.getElementById("jvm-lamp");
    var text    = document.getElementById("jvm-text");
    var elapsed = document.getElementById("jvm-elapsed");
    var status  = document.getElementById("status-msg");
    var t0      = Date.now();

    function say(message, barMessage) {
        if (text) {
            text.textContent = message;
        }
        if (status && barMessage) {
            status.textContent = barMessage;
        }
    }

    if (typeof cheerpjInit !== "function") {
        lamp.textContent = "[ !! ]";
        say("cheerpj loader unavailable -- check network access to cjrtnc.leaningtech.com",
            "Applet not loaded.");
        return;
    }

    var timer = setInterval(function () {
        elapsed.textContent = ((Date.now() - t0) / 1000).toFixed(1) + "s";
    }, 100);

    say("initialising cheerpj 4.3 runtime (java 8)", "Loading applet...");

    cheerpjInit().then(function () {
        clearInterval(timer);
        elapsed.textContent = ((Date.now() - t0) / 1000).toFixed(1) + "s";
        lamp.textContent = "[ OK ]";
        lamp.className = "lamp ok";
        say("jvm ready -- " + appletCode + " loaded from HelloApplet.jar", "Applet running.");
    }).catch(function (err) {
        clearInterval(timer);
        lamp.textContent = "[ !! ]";
        say("jvm error: " + err, "Applet failed.");
    });
}());
