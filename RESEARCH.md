# Pulse – Research: similar real products

Products: **Atlassian Statuspage, PagerDuty, Better Stack, UptimeRobot**, plus **Uptime Kuma** for anti-flapping.
Researched 2026-11-18 using web search of vendor documentation (sources at the end).

> **✅ Verified**: found in vendor docs or product pages (or the project's own source for Uptime Kuma)
> **⚠️ Partial**: secondary source only, or the docs conflict
> **❓ Not found**: no evidence found, so no claim is made
> **💡 Recommendation**: my opinion for Pulse

---

## 1. Atlassian Statuspage

| Question | Findings |
|---|---|
| **Purpose / users** | ✅ A status communication product: companies tell customers and internal users about incidents and maintenance. **Not a monitor.** |
| **Health monitoring** | ✅ None built in. Component status is changed by hand, by email automation ("UP"/"DOWN" in the subject), through the REST API, or by integrations: monitoring tools (Datadog, New Relic, Librato, Pingdom) and alerting tools (PagerDuty, Opsgenie, VictorOps, xMatters). |
| **Incidents** | ✅ Public lifecycle **Investigating → Identified → Monitoring → Resolved**. Each update is timestamped and lists affected components. |
| **Customer communication** | ✅ Component levels: **Operational, Degraded Performance, Partial Outage, Major Outage, Maintenance**. ✅ Email subscribers get every update. SMS (paid plans) only on create, resolve and maintenance start. ✅ Sign-ups use reCAPTCHA and rate limits. |
| **Metrics** | ✅ "System metrics" can show data from Datadog, New Relic, Pingdom or Librato, or custom metrics posted through the API. ❓ Native uptime/SLO calculation not verified. |
| **Relevant to Pulse** | Fixed public status vocabulary; internal detail kept separate from the public message; timestamped public timeline. |

## 2. PagerDuty

| Question | Findings |
|---|---|
| **Purpose / users** | ✅ Incident response and on-call alerting for engineering and operations teams. |
| **Health monitoring** | ✅ Mainly **receives events** from monitoring tools through Events API v2 (trigger, acknowledge, resolve). Events need a summary, source and severity (critical, error, warning or info). ❓ Native synthetic checks not verified. |
| **Incidents** | ✅ **Triggered → Acknowledged → Resolved.** ✅ **Dedup key**: a trigger for an already-open incident is appended to it. ✅ Ack events for resolved or unknown incidents are **discarded**. ✅ Escalation policies; acknowledging stops escalation. ✅ By default, notifications fire on trigger, not on ack or resolve. ⚠️ Alert grouping exists; details not verified. |
| **Customer communication** | ✅ External Status Page built from "business services", with branding and email subscribers. Some doc versions also list Slack and webhooks. ⚠️ Plan availability conflicts between doc versions. |
| **Metrics** | ✅ **MTTA** = average time from incident creation to first ack (unacknowledged excluded). **MTTR** = creation → resolved (unresolved excluded). ✅ "Interruptions": responder notifications, with several channels to the same person counted once. |
| **Relevant to Pulse** | Dedup (one open incident per service), rejecting ack after resolve, exact MTTA/MTTR definitions. |

## 3. Better Stack (Uptime)

| Question | Findings |
|---|---|
| **Purpose / users** | ✅ Uptime monitoring, on-call and incident management, and status pages in one product, for engineering teams. |
| **Health monitoring** | ✅ Active checks with a request timeout. ✅ **The check frequency may not be shorter than the request timeout** (the configuration is rejected). |
| **Incidents** | ✅ **Confirmation period**: wait after a failure before opening an incident ("immediate" is an option). ✅ **Recovery period**: the monitor must stay up before the incident resolves. Their example is 30 s checks with a 1 min recovery, which needs 2 consecutive successes; **any failure resets the countdown**. ✅ Tiered escalation; ack stops it. ✅ "I'm unavailable" hands the incident to the next person. |
| **Customer communication** | ✅ Built-in status page, email subscriptions, embedded response-time charts, webhook subscriptions. |
| **Metrics** | ✅ Response-time charts. ❓ SLA or availability reporting not verified. |
| **Relevant to Pulse** | Confirmation and recovery periods (anti-flapping), validating that the timeout is less than the interval. |

## 4. UptimeRobot

| Question | Findings |
|---|---|
| **Purpose / users** | ✅ Simple website and service uptime monitoring for small teams (has a free tier). |
| **Health monitoring** | ✅ Monitor types: HTTP(S), Keyword, Ping, Port, DNS, Heartbeat (cron and background jobs), SSL. ⚠️ Fastest interval reported as 15 s or 30 s depending on the page; free plan 5 min. ✅ **A failed check is re-run from a second location before alerting.** |
| **Incidents** | ✅ Logs downtime with timestamps. Alerts by email, SMS, call and other channels, with escalation rules. ❓ No ack or notes workflow found. |
| **Customer communication** | ✅ Status pages built from chosen monitors or tags. Optional custom domain, password protection, and incident or maintenance announcements with email subscriptions. |
| **Metrics** | ✅ Uptime % and response-time chart. ⚠️ A third-party guide describes 24h/7d/30d/90d uptime windows and avg/min/max response time. ✅ The generic formula on UptimeRobot's site is (total − downtime) / total × 100; ❓ their exact method is not confirmed. |
| **Relevant to Pulse** | Several uptime windows; "confirm before alerting". |

## 5. Uptime Kuma (anti-flapping reference)

- ✅ **Retries**: after a failure the monitor goes **"Pending"** and retries before "Down". A separate (shorter) retry interval can be set.
- ⚠️ Pending → Up sends no notification (from source commits).
- ✅ Manual incident banners on the status page; maintenance windows (one-time or recurring) suppress false alerts.
- ⚠️ No subscriptions for status page visitors (third-party guide).
- ❓ No acknowledge workflow found; no SLO found.

---

## Comparison table

| Capability | Statuspage | PagerDuty | Better Stack | UptimeRobot | **Pulse** |
|---|---|---|---|---|---|
| Active health checks | ❌ (integrations) | ❓ mainly receives events | ✅ | ✅ | ✅ HTTP, own interval per service (D3) |
| Anti-flap rule | n/a | ✅ dedup key | ✅ confirmation + recovery periods | ✅ second-location re-check | ✅ 2 failures → Down, 2 successes → recovered (D6, D7) |
| Incident lifecycle | 4 public stages | Triggered / Ack / Resolved | Open / Ack / Resolved | downtime log | ✅ Open / Ack / Resolved + notes (D10) |
| Escalation / on-call | via integrations | ✅ | ✅ | ✅ rules | ❌ skipped |
| Public status page | ✅ core product | ✅ | ✅ | ✅ | ✅ (D13) |
| Status levels | 5 | n/a | ❓ | up / down | Healthy / Degraded / Down |
| Subscribers | email, SMS | email (+ Slack/webhook ⚠️) | email, webhook | email | ❌ cut (D18) |
| Uptime / SLO | ❓ | ❓ | ❓ | ✅ uptime % | ✅ 24 h availability against target + error budget (D14, D15) |
| MTTA / MTTR | ❓ | ✅ | ❓ | ❓ | ✅ + MTTD |
| Webhook notifications | ✅ | ✅ | ✅ | ✅ | ✅ open + resolve (D12) |

**Where Pulse is different** 💡: In what I could verify, no single product here is both an active monitor and a system that tracks per-service targets with an error budget. (Datadog does offer error budgets, but it's a broader observability product.)

---

## Five product design ideas 💡

1. **Confirm before opening, confirm before resolving** (Better Stack, Uptime Kuma) → D6 and D7.
2. **One open incident per service, with dedup** (PagerDuty) → D9.
3. **A strict lifecycle: reject ack or notes on resolved incidents** (PagerDuty) → D10.
4. **A fixed public vocabulary, kept apart from internal detail** (Statuspage) → D13. This makes the kit's `10.12.4.7` leak impossible by design.
5. **Exact metric definitions** (PagerDuty MTTA/MTTR) plus **MTTD**, which Pulse can measure because it runs the checks → PLAN §11.

*Also adopted:* timeout < interval validation (Better Stack) → D3 and D4. Notifications only on open and resolve (PagerDuty default) → D12.

## Three features to avoid (time limit) 💡

1. **Escalation policies and on-call rotations**: a product in themselves; "acknowledged by" covers the must-have.
2. **Customer subscriptions (email/SMS)**: delivery, unsubscribe and abuse protection (Statuspage needs reCAPTCHA and rate limits); "going further" only.
3. **Several check types and multi-region confirmation**: the kit only exposes HTTP `/health`; the 2-failure rule gives most of the benefit.

## Opportunities to make Pulse useful without overengineering 💡

- **"Since" timestamps** everywhere ("Down since 14:02, 6 min"): the first question both support and engineers ask.
- **Error budget in minutes** ("0.9 of 1.44 min left"), not just a percentage.
- **A failure-rate column** on the dashboard, so flaky services are visible without a special state (D11).
- **Scheduler lag as a metric**: shows whether Pulse itself is healthy (must-have #9).
- **"No data" shown honestly**, never as 100% (D14).
- **A useful webhook payload**: service, owner, status, timestamp, duration on resolve, incident ID.
- **The active rules shown on screen** ("Down = 2 failures in a row"): makes the demo easy to explain and the live threshold change trivial.

---

## Research gaps (stated honestly)

- UptimeRobot's exact uptime calculation, Better Stack's SLA reporting, and PagerDuty's alert grouping were not verified.
- PagerDuty's docs conflict on which plans include the external status page.
- Uptime Kuma details come from mirrored source commits and third-party guides, not official docs.
- I found no native SLO feature in Statuspage, Better Stack or PagerDuty. That is an absence of evidence, not proof.

## Sources

- Statuspage: [Enable subscribers](https://support.atlassian.com/statuspage/docs/enable-subscribers/) · [Integrations](https://www.atlassian.com/software/statuspage/integrations) · [System metrics](https://support.atlassian.com/statuspage/docs/show-real-time-performance-with-system-metrics) · [Components](https://support.atlassian.com/statuspage/docs/create-and-manage-your-components/) · [How Statuspage works](https://support.atlassian.com/statuspage/docs/learn-how-statuspage-works/) · [Datadog → Statuspage](https://docs.datadoghq.com/monitors/guide/integrate-monitors-with-statuspage) · [Component levels example](https://guard.status.atlassian.com) · [Incident example](https://metastatuspage.com/incidents/n141hfm11db9)
- PagerDuty: [Incidents](https://support.pagerduty.com/docs/incidents) · [Analytics Dashboard](https://support.pagerduty.com/docs/analytics-dashboard) · [External Status Page](https://support.pagerduty.com/main/docs/external-status-page) · [Status Pages overview](https://support.pagerduty.com/docs/status-pages) · [Dedup key behaviour (Ansible module docs)](https://docs.ansible.com/projects/ansible/latest/collections/community/general/pagerduty_alert_module.html) · [Events API v2](https://blog.incidenthub.cloud/Integrate-Your-Monitoring-System-With-PagerDutys-Events-API-V2) · [Yale PagerDuty guide](https://enterprisemonitoring.yale.edu/welcome-to-pagerduty/incidents)
- Better Stack: [Confirmation and recovery period](https://betterstack.com/docs/uptime/confirmation-and-recovery-period/) · [Request timeout](https://betterstack.com/docs/uptime/request-timeout/) · [On-call](https://betterstack.com/docs/uptime/getting-started-with-oncall-v2/) · [Escalation changelog](https://betterstack.com/community/blog/request-cover-escalate-to-next-person.md) · [Webhook subscriptions](https://betterstack.com/docs/uptime/status-pages/subscribing-to-status-updates/subscribing-with-webhooks/) · [Incident management](https://betterstack.com/incident-management)
- UptimeRobot: [Homepage](https://uptimerobot.com/) · [Monitoring types](https://uptimerobot.com/knowledge-hub/monitoring/ultimate-guide-to-uptime-monitoring-types/) · [Availability monitoring](https://uptimerobot.com/availability-monitoring/) · [Third-party status page guide](https://formpipelegal.deskpro.com/kb/articles/uptime-robot)
- Uptime Kuma: [Retry interval commit](https://git.hackliberty.org/Infra-Mirrors/uptime-kuma/commit/f8f9f59464ab8fcc6bf57b159d51f2660b74152c) · [Pending/notification commit](https://git.hackliberty.org/Infra-Mirrors/uptime-kuma/commit/72783fd94c0dc0583c2ade12ad026d5e396ad75d) · [borgmatic retries example](https://projects.torsion.org/borgmatic-collective/borgmatic/commit/3e6004363237534400636641fbe036851af34a43) · [Status pages guide](https://selfhosted-sh.pages.dev/uptime-kuma-status-pages) · [Incidents & maintenance guide](https://www.dchost.com/blog/en/?p=4701)
- Error budgets: [Datadog error budget](https://docs.datadoghq.com/service_management/service_level_objectives/error_budget/) · [isdown.app](https://isdown.app/blog/error-budget-sre)
