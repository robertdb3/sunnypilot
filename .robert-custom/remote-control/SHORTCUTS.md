# iPhone Shortcut blueprints

Create these only after the `visual` or `speed` release stage is promoted and enabled. Each
Shortcut stores two private Text actions:

- `BaseURL`: `https://comma3x.<your-tailnet>.ts.net`
- `BearerToken`: the single line from `/data/custompilot/commandd.token`

Every **Get Contents of URL** action uses JSON, includes `Authorization: Bearer <BearerToken>`,
and calls the `BaseURL`. Do not add `Tailscale-User-Login`: Tailscale Serve injects that header
after authenticating the iPhone. Turn on Tailscale VPN On Demand for the iPhone.

## Comma brightness

1. Ask for Input: “Brightness percentage, auto, auto dark, or screen off?” (Text).
2. Normalize spoken “auto dark” to `auto-dark` and “screen off” to `screen-off`. Numeric input
   must be 5–100 and divisible by 5; otherwise speak a local error and stop.
3. POST `/v1/visual` with `{ "brightness": <normalized value> }`.
4. If the JSON response has `ok=true`, Speak Text from `spoken`; otherwise speak `reason`.

## Comma camera / Comma 3D

POST `/v1/visual` with `{ "view": "camera" }` or `{ "view": "3d" }`, then speak `spoken`.

## Comma map on / Comma map off

POST `/v1/visual` with `{ "map": "on" }` or `{ "map": "off" }`, then speak `spoken`.

## Comma set speed

1. Ask for Number: “What cruise speed?” and convert to an integer.
2. POST `/v1/speed/prepare` with `{ "target_mph": <number> }`.
3. On failure, speak `reason` and stop. On success, speak: “Set cruise from `current_mph` to
   `target_mph` miles per hour?”
4. Use **Choose from Menu** with exactly `Confirm` and `Cancel`. Siri’s recognized affirmative
   chooses Confirm; anything else stops without another request.
5. POST `/v1/speed/confirm` with `{ "confirmation_token": "<confirmation_token>" }`.
6. Poll GET `/v1/speed/status?id=<command_id>` no more than once per second for 13 seconds.
7. Speak success only when `outcome` is `holding`. For `aborted`, `rejected`, `cleared`, a network
   error, or the polling deadline, speak the normalized outcome and `reason`.

## Comma resume speed assist

POST `/v1/speed/resume-assist` with `{}` and speak `spoken`.

Network errors must go to a Shortcut **Stop and Output** path that says “Comma command failed; no
change was made.” Never retry a POST confirmation automatically.

Apple does not provide a supported text format that this repository can silently install into an
iPhone. Build these in Shortcuts after enrollment, then export the signed personal shortcuts from
your own Apple account. Do not publish an exported Shortcut containing the private BaseURL or
bearer token.
