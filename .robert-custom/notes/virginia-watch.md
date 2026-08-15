# Virginia reckless speed watch

**This is a driver aid, not legal advice, and not a guarantee of anything.** It watches your car's
speed, which will differ from an officer's radar even after calibration. Treat it as a reminder,
not a shield.

## The law it's built around

[Virginia Code § 46.2-862](https://law.lis.virginia.gov/vacode/title46.2/chapter8/section46.2-862/),
verbatim:

> A person is guilty of reckless driving who drives a motor vehicle on the highways in the
> Commonwealth (i) at a speed of 20 miles per hour or more in excess of the applicable maximum
> speed limit or (ii) in excess of 85 miles per hour regardless of the applicable maximum speed
> limit.

Class 1 misdemeanor — up to 12 months in jail, up to $2,500, licence suspension up to 6 months, and
a permanent criminal record. The Commonwealth does not have to prove you knew your speed.

**It is 85, not 80.** 80 was correct until HB 1442 raised it on 1 July 2020. A test asserts the
constants so nobody "corrects" them back.

## What it does

| | |
|---|---|
| Entering Virginia | One spoken/chime alert, "Entering Virginia" |
| Crossing the threshold | One audible alert, **once** |
| While still above | Pulsing red border, no further sound |
| Dropping back under | Border fades out, alert re-arms |

The re-arm needs you to drop 3 mph below the threshold, not just touch it — hovering at the line
would otherwise produce a stream of alerts, which is exactly what you asked to avoid.

## Settings

Visuals → **Virginia Reckless Speed Watch** (default off), and **Reckless Watch Alert Speed**
(default **82 mph**, adjustable 60–90).

Why 82 rather than 85: the statute is "in excess of 85", so 82 gives you a few mph to notice and
react, and absorbs any residual speedometer error.

## The 20-over rule and its limits

Fires at posted limit + 20, using the resolved speed limit from your offline OSM data. **When no
speed limit is known for the road, this check stays silent** rather than guessing — OSM coverage is
patchy and a wrong limit would produce false alarms. The absolute threshold still applies
everywhere regardless.

So on a road with no limit data you are covered for the 85 rule but not the 20-over rule. That is
the honest trade; the alternative is crying wolf.

## Geofence

Point-in-polygon against a vendored 1,268-point Virginia outline (US Census-derived, public
domain), including the Eastern Shore and the bay islands as separate rings.

A coarser 65-point outline was rejected: it disagreed near real borders, and knowing which side of
a border you are on *is* the feature. The vendored one was validated against the Bristol VA/TN
line, where State Street literally is the border — it transitions to Virginia at lat 36.596, which
is State Street.

Crossings need 5 consecutive GPS fixes on the new side before they count, so driving along a border
road doesn't chatter. Fixes with horizontal accuracy worse than 50 m are ignored rather than
believed, so a tunnel doesn't read as leaving the state. Starting a drive already inside Virginia
is not announced as a crossing.

## Speedometer accuracy

Your car reads 2-5 mph high, which matters here — an alert fired off an inflated number cries wolf.

The cause: `wheelSpeedFactor` defaults to `1.0` and Subaru never overrides it, while Toyota sets
`1.035` and Honda `1.025` for exactly this. To fix it properly:

```bash
python3 tools/calibrate_wheel_speed.py --minutes 10
```

Drive steady highway while it runs. It compares `vEgo` against GPS ground speed during cruise,
filters to conditions where both are trustworthy, and prints the `wheelSpeedFactor` to set in
`opendbc/car/subaru/interface.py`. **That number has to come from your drive — it cannot be
guessed.**

Note `TrueVEgoUI` will not help: Subaru never sets `vEgoCluster`, so `interfaces.py:280` already
makes it equal `vEgo`. The toggle is a no-op on this car.
