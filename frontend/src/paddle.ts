// Card checkout through Paddle (the merchant of record). The subscription it
// opens carries the organization a license must name on THIS deployment —
// `licensed_to` from /v1/license/status: the tenant on hosted, AXOR_ORG on a
// self-hosted install — and whether this is the vendor's hosted plane (the
// same status's `hosted`), so axor-billing knows whose license to issue and
// whether to install it on the hosted plane or mail it to a self-hosted one.
//
// The client token and price ids are public values (Paddle.js runs in the
// browser): they ship in the build, self-hosted images included. Unset, every
// paid plan keeps the mailto / VITE_CHECKOUT_URL link it had.
import type { Rung } from "./quote";

type Env = Record<string, string | undefined>;
const ENV: Env = (import.meta as unknown as { env?: Env }).env ?? {};

export const PADDLE_JS = "https://cdn.paddle.com/paddle/v2/paddle.js";

const TOKEN = ENV.VITE_PADDLE_CLIENT_TOKEN ?? "";
const SANDBOX = ENV.VITE_PADDLE_ENV === "sandbox";

function prices(): Partial<Record<Rung, string>> {
  try {
    return JSON.parse(ENV.VITE_PADDLE_PRICES || "{}");
  } catch {
    return {};
  }
}
const PRICES = prices();

/** The Paddle price this rung is sold at, when card checkout is set up for it. */
export function paddlePrice(rung: Rung): string | undefined {
  return TOKEN ? PRICES[rung] : undefined;
}

interface PaddleJs {
  Environment: { set: (env: string) => void };
  Initialize: (opts: { token: string }) => void;
  Checkout: { open: (opts: unknown) => void };
}

let loading: Promise<PaddleJs> | null = null;

function load(): Promise<PaddleJs> {
  loading ??= new Promise<PaddleJs>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = PADDLE_JS;
    script.onload = () => {
      const paddle = (window as unknown as { Paddle?: PaddleJs }).Paddle;
      if (!paddle) return reject(new Error("Paddle.js did not load"));
      if (SANDBOX) paddle.Environment.set("sandbox");
      paddle.Initialize({ token: TOKEN });
      resolve(paddle);
    };
    script.onerror = () => { loading = null; reject(new Error("Paddle.js did not load")); };
    document.head.appendChild(script);
  });
  return loading;
}

export async function openCheckout(priceId: string, org: string, hosted: boolean, email?: string): Promise<void> {
  const paddle = await load();
  paddle.Checkout.open({
    items: [{ priceId, quantity: 1 }],
    customData: { org, hosted },
    // the signed-in address, so nobody types it again (QA-014)
    ...(email ? { customer: { email } } : {}),
  });
}
