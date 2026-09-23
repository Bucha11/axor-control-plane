// Runs the Paddle checkout for a transaction axor-identity opened, then sends
// the payer back to the app that started it. Served from the app's own origin
// (/identity/v1/billing/pay), so the return is same-site.
(async function () {
  const status = document.getElementById("status");
  const fail = (msg, back) => {
    status.textContent = msg + " ";
    if (back) {
      const a = document.createElement("a");
      a.href = back;
      a.textContent = "Back to the app";
      status.appendChild(a);
    }
  };
  const txn = new URLSearchParams(location.search).get("_ptxn");
  if (!txn) return fail("No checkout to open.");
  let config, checkout;
  try {
    [config, checkout] = await Promise.all([
      fetch("config").then((r) => r.json()),
      fetch("checkouts/" + encodeURIComponent(txn)).then((r) => {
        if (!r.ok) throw new Error("unknown checkout");
        return r.json();
      }),
    ]);
  } catch (e) {
    return fail("This checkout link is not valid.");
  }
  const back = (outcome) => {
    const url = new URL(checkout.return_url);
    url.searchParams.set("billing", outcome);
    return url.toString();
  };
  if (!config.enabled || typeof Paddle === "undefined") {
    return fail("Checkout is unavailable right now.", back("unavailable"));
  }
  if (config.environment === "sandbox") Paddle.Environment.set("sandbox");
  // Initialize opens the checkout for the ?_ptxn= transaction by itself.
  Paddle.Initialize({
    token: config.client_token,
    checkout: { settings: { displayMode: "overlay", successUrl: back("success") } },
    eventCallback: (event) => {
      if (event.name === "checkout.closed") location.href = back("canceled");
    },
  });
})();
