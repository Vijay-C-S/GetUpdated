# SEO QA Checklist

Run this before publishing major updates.

## Quick Run

```powershell
pwsh -File .\seo-qa-check.ps1
```

## Automated Invariants (keep these true)

Every indexable page must have:

1. `title` **≤ 60 characters**, keyword front-loaded (Google truncates beyond ~60).
2. `meta description` **110–160 characters**, unique across the site.
3. `canonical` — self-referencing and byte-identical to `og:url` and every `hreflang` href.
4. `meta robots` = `index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1`.
   The `max-image-preview:large` token is what unlocks large thumbnails in SERPs and Google Discover.
5. `og:image` **plus** `og:image:width`, `og:image:height`, `og:image:alt`, `og:image:secure_url`.
6. `twitter:card`, `twitter:site`, `twitter:image`.
7. Exactly one `<h1>`.
8. Valid JSON-LD (`Article` on blogs, `JobPosting` where a job is described).
9. Every `<img>` has `alt`, `width`, `height`, `decoding="async"`, and `loading`
   (`eager` + `fetchpriority="high"` for the hero/LCP image, `lazy` for everything else).
10. Blog heroes are preloaded: `<link rel="preload" as="image" href="…" fetchpriority="high">`.

## Structured Data Rules

- `Article` requires: `headline` (≤110 chars), `image`, `datePublished`, `dateModified`,
  `author`, `publisher`, `mainEntityOfPage`, `inLanguage`.
- `JobPosting` requires: `title`, `description`, `datePosted`, `validThrough`,
  `hiringOrganization`, and **either** `jobLocation` **or**
  `jobLocationType: TELECOMMUTE` + `applicantLocationRequirements`.
- **Expired jobs:** when `validThrough` passes, Google drops the posting from Google Jobs.
  Leaving a past `validThrough` is correct and honest — do **not** roll dates forward to keep
  a closed job listed. Instead, remove the `JobPosting` block (keep the article) once the
  role closes, or update it with the genuine new deadline.
- Do **not** add `FAQPage` markup unless the same Q&A is visible on the page. Google restricted
  FAQ rich results to authoritative government/health sites in 2023, so it adds risk without reward.

## Manual Checks

1. Validate a sample blog and a job page in Google's Rich Results Test.
2. Confirm `robots.txt` and `sitemap.xml` are reachable and current.
3. Check mobile rendering at 375px, 576px, and 768px — no horizontal overflow.
4. Spot-check social cards in an Open Graph debugger.
5. Verify ad scripts stay `async`, and `target="_blank"` links keep `rel="noopener noreferrer"`.
6. Re-run Search Console → Page Indexing after a large publish.

## Images

Keep every image **≤ ~200 KB** and **≤ 1400 px wide**. Source renders are often 2816 px wide and
600–780 KB, which alone can push LCP past 4s on Indian mobile networks.

```powershell
python -c "from PIL import Image; import glob,os; [print(os.path.getsize(f), Image.open(f).size, f) for f in glob.glob('images/*') if os.path.getsize(f)>200000]"
```

## Known Hosting Constraint

The site is served by **GitHub Pages** (see `CNAME` + the `Vijay-C-S/GetUpdated` remote).
GitHub Pages does **not** read `.htaccess` — it is an Apache config file and is inert here.
That means the following are **not** currently applied in production:

- Security headers (CSP, X-Frame-Options, Referrer-Policy, HSTS)
- Custom cache-control / `Expires` headers
- HTTP→HTTPS and non-www→www redirects (GitHub Pages does handle HTTPS itself)

`.htaccess` is kept for a future move to Apache hosting. To get these headers today, put the site
behind Cloudflare (free) and set them via Transform Rules / Cache Rules. This does not block
ranking, but the headers are a "best practices" line item in Lighthouse.
