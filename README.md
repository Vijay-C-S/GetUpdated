# GetUpdated

Static site for [getupdated.in](https://www.getupdated.in): job openings, government recruitment,
and tech/AI news for freshers. Hosted on GitHub Pages (see `CNAME`); every push to `main` goes live.

## Layout

```
Get_Updated/
├── index.html, blogs.html, jobs.html, job-detail.html, tech-news.html, offers.html
├── about.html, disclaimer.html, privacy-policy.html, terms-of-service.html, 404.html
├── styles.css            # site-wide CSS (minified); pages also carry their own inline <style>
├── blog-styles.css       # extra styles used by blog posts
├── script.js             # site-wide JS (minified): nav, theme, job filters, contact form, WhatsApp button
├── js/engagement-analytics.js   # used by jobs.html, job-detail.html, offers.html
├── blogs/*.html          # one self-contained page per post
├── images/               # post/banner images (.webp preferred)
├── sitemap.xml, robots.txt, manifest.json, ads.txt
├── *.txt (hex names)     # IndexNow key files - keep them
├── google*.html          # Search Console verification - keep it
├── automation/           # the job-blog bot (see automation/README.md)
└── tools/update_heads.py # bulk-edit the <head> of many pages at once
```

## Adding content

Most job posts are written and published by the bot in `automation/`: it creates
`blogs/<slug>.html` and `images/<slug>.webp`, adds cards to `index.html` and `blogs.html`, and adds
the URL to `sitemap.xml`. See [automation/README.md](automation/README.md).

To add a post by hand, copy a recent file in `blogs/`, then add:
1. a card on `index.html` and `blogs.html` (copy an existing one),
2. a `<url>` entry in `sitemap.xml`.

## Changing something on every page

Pages don't share a layout file, so a change to the `<head>` (a script, a stylesheet, a meta tag)
has to be made in every page. Use the helper rather than editing by hand:

```bash
python tools/update_heads.py --help
python tools/update_heads.py replace 'old snippet' 'new snippet'           # dry run
python tools/update_heads.py replace 'old snippet' 'new snippet' --write
```

Also update `automation/templates/post_template.html` so future posts get the same change.

## Contact form

The form on `index.html` sends through EmailJS. Fill in `EMAILJS_SERVICE_ID`,
`EMAILJS_TEMPLATE_ID` and `EMAILJS_PUBLIC_KEY` near the bottom of `index.html` (template variables:
`{{from_name}} {{from_email}} {{subject}} {{message}}`). Until all three are set, submitting the
form opens the visitor's email app addressed to contact.us.getupdated@gmail.com.

## Checks

- `seo-qa-check.ps1`: SEO checks across all pages (see `SEO_QA_CHECKLIST.md`).
- `python -m pytest automation/tests`: tests for the bot's pure logic.

## Note on `.htaccess`

GitHub Pages ignores `.htaccess`, so its headers and CSP have no effect today. If the site
ever moves to Apache, the CSP needs `https://cdn.jsdelivr.net` added to `script-src` (EmailJS
loads from there).
