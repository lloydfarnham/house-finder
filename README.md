# House Hunt 🏡

Lloyd & Meg's rental search. A GitHub Action searches Rightmove, Zoopla, OnTheMarket and OpenRent
every hour from 8am to 7pm (UK time). It filters the results to our criteria, merges duplicate
listings and sends a push notification for new properties and price drops. The web app
(GitHub Pages, `docs/`) shows the results and a shared "Saved" list with notes and viewing progress.

**Criteria** (edit `config.json`)
- To rent, 2–3 bedrooms, up to £2,000 pcm
- No more than a 10 minute walk to: Bethnal Green, Mile End, Stepney Green, Bow Road, Bromley-by-Bow
  (main areas), or Leyton, Leytonstone, Wanstead, Snaresbrook (second choice). Nothing nearer Stratford.
- Leaves out house shares, student/retirement lets, let-agreed listings and listings with no living room
- Leaves out anything whose stated availability date is after 31 January 2027
- Shows the cycle time to Euston, plus garden/balcony tags

## Notifications
Install the **ntfy** app (iOS/Android) → **+** → subscribe to the topic `lloyd-meg-homes-8ded8520df`.

## Shared saved list (Supabase)
1. Create a free project at supabase.com.
2. SQL Editor → paste `supabase/schema.sql` → Run.
3. Project Settings → API: copy the Project URL and the anon / publishable key into `docs/config.js`.

## Running by hand
Actions → *Search for properties* → **Run workflow**. Tick *debug* to save the raw pages to the `debug-dump` branch.

Locally: `pip install -r requirements.txt && FORCE=1 NO_NOTIFY=1 python -m scraper.main`
