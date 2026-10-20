# Bot contact page

The page every crawler request links to in its `User-Agent`:
`agentready-bot/<version> (+<contact_url>)`. One static HTML file, no build step.

## Deploy on Vercel

Either import the repository in the Vercel dashboard and set **Root Directory** to
`bot-page` (framework preset: Other, no build command), or from this folder:

```
npx vercel deploy --prod
```

Then put the production URL in `config/crawl.yaml`:

```yaml
contact_url: https://<your-project>.vercel.app/
```

Keep this URL stable: it appears in government servers' logs. When the web app (T20)
is live, this page can stay or redirect to the web app's bot page.

## If you change crawler behaviour

The "rules it follows" table states the values in `config/crawl.yaml` (5-second delay,
22:00-06:00 IST window, 500 pages, 50 MB, retries). Update the page when they change.
