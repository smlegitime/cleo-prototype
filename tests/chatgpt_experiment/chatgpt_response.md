Below is an example **Bluesky / ATProto labeler configuration** (using the `app.bsky.labeler.service` schema structure) that reflects the design decisions from your discussion:

* Labeler name and description emphasize **community operation** and **regular updates**.
* A **positive badge label** for verified community members (vouched by two existing verified members).
* A **warning label** for accounts flagged for harassment or impersonation.
* The harassment label shows a **warning without heavy blurring**, so the account isn’t hidden but is clearly flagged.
* Verification **may be revoked if bad faith behavior is discovered**.

```json
{
  "$type": "app.bsky.labeler.service",
  "name": "Queer Arts Collective — Community Safety",
  "description": "Community-operated safety labeler run by members of the Queer Arts Collective on Bluesky. Labels are applied and reviewed by volunteer moderators and updated regularly. The Verified Community Member label is granted when an account is vouched for by two existing verified members. Verification may be revoked if a member is later found to have acted in bad faith or violated community safety expectations.",
  "policies": {
    "labelValues": [
      {
        "identifier": "verified-community-member",
        "severity": "informational",
        "blurs": "none",
        "defaultSetting": "ignore",
        "adultOnly": false,
        "description": "Verified member of the Queer Arts Collective community. Verification requires vouching from two existing verified members. This label acts as a positive badge indicating trusted participation in the community. Verification may be revoked if the account is later found to have acted in bad faith."
      },
      {
        "identifier": "harassment-or-impersonation",
        "severity": "alert",
        "blurs": "none",
        "defaultSetting": "warn",
        "adultOnly": false,
        "description": "Account flagged by the Queer Arts Collective community safety team for harassment, impersonation, or related harmful conduct. Subscribers to this labeler will see a warning by default. This label is intended to alert viewers while keeping the account visible."
      }
    ]
  },
  "createdBy": "community"
}
```

### Why these settings

* **`verified-community-member`**

  * `severity: informational` → positive badge style label
  * `blurs: none` → never hides or obscures the account
  * `defaultSetting: ignore` → subscribers see the badge but it doesn’t block content

* **`harassment-or-impersonation`**

  * `severity: alert` → communicates risk
  * `blurs: none` → keeps the account visible (per your request)
  * `defaultSetting: warn` → users see a warning banner by default

### Optional improvement some community labelers add

You might later add a **third label** like `under-review` for transparency during investigations, but it wasn’t part of your discussion so I didn’t include it.

---

If you want, I can also:

* Convert this into a **ready-to-publish `did:plc` labeler service record**, or
* Suggest **governance guidelines** many Bluesky community labelers publish alongside their config (appeals, evidence standards, revocation process, etc.).
