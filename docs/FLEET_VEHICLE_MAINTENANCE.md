# Sapphire Fountains — Fleet Vehicle Maintenance

_Schedule + standard operating procedure for routine company-vehicle maintenance,
and how it is tracked in ERPNext (the **Fleet Maintenance** module, v1.138.0)._

## Schedule at a glance

| Cadence | Tasks | Tracked as |
|---|---|---|
| **Daily** | Check gas level; refill if at or below half | Standing driver instruction (not logged) |
| **Weekly** | Inventory/stock, fluids (oil + washer), tire pressure, car wash + interior | Vehicle Maintenance Log — *Weekly* |
| **Every 3 months** | Oil change | Vehicle Maintenance Log — *Oil Change (3-Month)* |
| **Every 6 months** | Dealership check-up; replace windshield wipers | Vehicle Maintenance Log — *Dealership Check-Up* / *Windshield Wipers* |

Cadence intervals are configurable in **ERPNext Enhancements Settings → Fleet
Maintenance**; the values above are the defaults.

---

## Daily — every driver, every day

> A standing instruction, not a tracked form (logging every vehicle daily would
> bury the real maintenance records). Post it where drivers will see it.

- **Check the gas level. Refill the tank if it is at or below halfway.**

---

## Weekly

> The routine upkeep form. Open a **Vehicle Maintenance Log**, pick the vehicle,
> set the type to **Weekly**, and the checklist below loads automatically. Set each
> item's status (OK / Action Needed / N/A) as you go, then **Submit**.

- **Definition:** the weekly service checklist —
  - Check & restock vehicle inventory/stock
  - Check engine oil level *(required)*
  - Check windshield washer fluid *(required)*
  - Check tire pressure, all tires *(required)*
  - Car wash (exterior)
  - Interior cleaning
- **Why it matters:** catches low fluids, soft tires, and stock-outs before they
  become a breakdown or a missed-job problem.
- **On submit:** the vehicle's *Last Weekly Service* date rolls forward and *Weekly
  Service Due* is recomputed (last + 7 days by default).

---

## Every 3 months — Oil change

> Time it from the calendar or, once the system has a baseline, the vehicle's
> **Oil Change Due** date. Log type **Oil Change (3-Month)**.

- **Definition:** oil & filter changed *(required)*; reset oil-life indicator; check
  other fluid levels.
- **Why it matters:** the core engine-longevity service; the most expensive thing to
  get wrong by skipping.
- **Target:** every 3 months (configurable).
- **On submit:** *Last Oil Change* rolls forward; *Oil Change Due* = last + 3 months.

---

## Every 6 months — Dealership check-up & wipers

> Two separate log types so each rolls its own due date. Time them from the calendar
> or the vehicle's **Due** dates.

- **Dealership Check-Up (6-Month)** — dealership inspection/service completed
  *(required)*; review & note any dealership recommendations.
- **Windshield Wipers (6-Month)** — wiper blades replaced *(required)*.
- **Why it matters:** the dealership visit catches manufacturer-recommended and
  safety items; wipers are a cheap, twice-a-year safety must.
- **On submit:** the matching *Last …* date rolls forward; the *… Due* date =
  last + 6 months.

---

## How due tracking & reminders work

- **Each Fleet Vehicle** shows a headline **Maintenance Status**: `No Data`, `OK`,
  `Due Soon`, or `Overdue`. It is derived from the last-done dates + the intervals,
  refreshed on every save and by a **nightly job** so it ages on its own.
- **`Due Soon`** lights up within the configurable window (default 7 days) before any
  service is due; **`Overdue`** once any due date has passed.
- **A baseline is required.** A cadence is only tracked once it has a last-done date —
  either log the first service, or seed the `Last …` field on the Fleet Vehicle for a
  service done before go-live. Without a baseline, that cadence stays silent.
- **Reminders:** when enabled, the day a vehicle newly becomes Due Soon / Overdue, a
  desk **notification** goes to fleet managers (users with the *Fleet Manager* or
  *Maintenance Manager* role, else System Managers).
- **The "Due" dashboard** is the Fleet Vehicle list: filter or sort by *Maintenance
  Status* to see what needs attention.

## Printing a blank checklist for the vehicle

Open a Vehicle Maintenance Log, pick the type, then **Menu → Print → Vehicle
Maintenance Checklist**. Printing a *draft* gives a blank sheet (☐ to tick) to keep
in the vehicle; printing a *submitted* log is the record of what was done.

## Roles

| Role | Can |
|---|---|
| **Fleet Manager** *(seeded; assign post-deploy)* | Manage vehicles + logs; receive reminders |
| **Maintenance Manager** *(stock)* | Manage vehicles + logs; receive reminders |
| **Maintenance User** *(stock)* | Create & submit logs; read vehicles |
| **System Manager** | Everything |

## Setup checklist (operator)

1. **Enable** ERPNext Enhancements Settings → Fleet Maintenance (`Enable Fleet
   Maintenance`). Adjust intervals / Due Soon window if the defaults don't fit.
2. **Assign the Fleet Manager role** to whoever owns the fleet (or rely on Maintenance
   Manager / System Manager for reminders).
3. **Create a Fleet Vehicle** per vehicle. Seed the `Last …` dates for services already
   done so due tracking starts immediately.
4. **Log services going forward** — Weekly each week; Oil Change / Dealership / Wipers
   on cadence. Each submit advances the due dates automatically.
