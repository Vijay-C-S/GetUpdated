# GetUpdated job-blog automation

Finds new job postings, writes a blog post in the site's house style, publishes it to
getupdated.in, and sends you a ready-to-paste WhatsApp message on Telegram. Runs free on
the Oracle VM using your Claude subscription (Claude Code headless).

```
cron (every 2h, 8am-10pm IST)
  1. watchdog.py     feeds + official company job APIs -> new leads (no AI)
  2. filter_jobs.py  Haiku keeps entry-level IT/EC and HR (Bengaluru/WFH), central + Karnataka govt
  3. Telegram:       🆕 NEW LEAD  [✍️ Write draft] [❌ Ignore]        <- you decide
bot.py (always on)
  4. ✍️ -> writer.py  Python pre-fetches official pages; Sonnet verifies + writes the article;
                     Python builds the page from templates/ and adds the company image
  5. Telegram:       draft image + facts + full HTML  [✅ Publish] [❌ Skip]   <- you decide
  6. ✅ -> publisher.py  post + image + homepage/blogs cards + sitemap, commit, push, wait live
  7. Telegram:       image + ready-to-paste WhatsApp text with the link
```

## Safety rails
- Two approvals: nothing is drafted without your ✍️ and nothing goes live without your ✅.
- No daily cap by default (`max_posts_per_day: 0`).
- House rules enforced in code: clickable official links only in the "Official Links" section;
  vacancy counts below 100 are never mentioned.
- SEO on every post: sitemap.xml entry (validated before commit), canonical, OG/Twitter tags,
  Article + JobPosting + FAQ + Breadcrumb JSON-LD, and an IndexNow ping (Bing, Yandex, ChatGPT search)
  once the page is live. Google picks up the sitemap from robots.txt and Search Console.
- PDFs are never read (too many tokens): facts come from official HTML pages and the job-site article; PDF
  links go in the post as links. If no official link/source confirms the job, the bot asks you
  `❓ CAN'T CONFIRM [✅ Exists, write it] [❌ No / fake]` and writes from the job-site text on ✅.
- The writer never skips a job on its own. If it thinks a job is closed / not entry level / needs
  experience, or writing fails, the bot asks once: `🤔 WRITER WANTS TO SKIP [✅ Write it anyway] [❌ Skip]`
  (or `⚠️ WRITING FAILED [🔁 Try again] [❌ Skip]`). ✅ reruns it with your override and the writer
  must write it; ❌ skips it. You are asked only once per job.
- Every such question also has `🔗 I'll send the link`: tap it, paste the official apply link as your
  next message, and the writer uses that link as the official source and apply link (`/cancel` brings
  the question back).
- If a finished draft's apply link is another job site's article (FreeJobAlert, FreshersNow, ...) or
  just a homepage, you are asked before seeing the draft: `🔗 APPLY LINK IS NOT OFFICIAL
  [✅ Keep that link] [❌ Skip] [🔗 I'll send the link]`. Your link is swapped into the written draft
  (no new AI run), then the draft comes for Publish / Skip as usual.
- The writer may only write in `state/drafts/`. Any other repo change is reverted automatically.
- Drafts are validated before you see them: size, sections, slug, image path, valid JSON-LD, ads/analytics head.
- Duplicate checks run against existing `blogs/` slugs and earlier leads.
- Secrets live only in `automation/.env` on the VM (gitignored).

## One-time setup on the Oracle VM

1. **Telegram bot.** In Telegram open `@BotFather`, send `/newbot`, and copy the token.
2. **GitHub push access from the VM.**
   ```bash
   ssh-keygen -t ed25519 -C "getupdated-vm" -f ~/.ssh/id_ed25519 -N ""
   cat ~/.ssh/id_ed25519.pub
   ```
   On GitHub, open the repo's **Settings → Deploy keys → Add deploy key**, paste the key, and tick **Allow write access**.
3. **Clone and install.**
   ```bash
   git clone git@github.com:Vijay-C-S/GetUpdated.git ~/GetUpdated
   cd ~/GetUpdated
   git config user.name "GetUpdated Bot"
   git config user.email "<your GitHub noreply email>"
   bash automation/setup/install.sh
   ```
4. **Log in to Claude Code once.** Run `claude`, then `/login` (open the link on your phone or PC), then `/exit`.
5. **Add the bot token.** Run `nano automation/.env` and set `TELEGRAM_BOT_TOKEN=...`.
6. **Start the bot.**
   ```bash
   sudo systemctl start getupdated-bot
   ```
7. **Get your chat ID.** Send any message to your bot. It replies with your chat ID. Put `TELEGRAM_CHAT_ID=<id>` in `.env`, then run:
   ```bash
   sudo systemctl restart getupdated-bot
   ```
8. **Test.** Send `/run` to the bot. To test a single job, send `/post <job link>`.

## Where leads come from
- **Official company portals (60 companies):** Greenhouse, Lever, Ashby and SmartRecruiters job APIs. Only entry-level
  Bengaluru or remote roles posted in the last 21 days are kept. Workday portals (NVIDIA, Intel, Qualcomm, TI...) block
  datacenter IPs, so they can't be used.
- **Official govt/bank pages:** KEA, SBI, ISRO, Canara Bank, Karnataka Bank, ECIL, Bank of Baroda, C-DAC. A new link
  on the page becomes a lead. KPSC, BEL, DRDO, RRB, IBPS and ONGC refuse the VM, so those come through the feeds below.
- **Aggregator feeds:** FreeJobAlert, KarnatakaCareers, FreshersNow, OffCampusJobs4u, FresherOpenings.
- **LinkedIn / Naukri / Indeed / Internshala job alerts:** read from an email inbox (`email_alerts.py`). The sites
  themselves are never scraped (their terms forbid it). Setup: a separate Gmail account, 2-Step Verification, an App
  Password, then `ALERTS_IMAP_USER` and `ALERTS_IMAP_PASSWORD` in `.env`, then subscribe that account to job alerts.
- **Backfill (daily, 09:15 IST):** `backfill.py` has Sonnet web-search central + Karnataka govt recruitments
  notified earlier that are still open (last date at least `backfill_min_days_left` days away, fresher-eligible
  posts only) and that we haven't covered. Each one arrives as a normal lead, closing soonest first.
- **WhatsApp channels / anything else:** forward or paste the post to the bot. It becomes a lead with ✍️/❌ buttons.
  WhatsApp has no API for reading channels, and unofficial readers get numbers banned.

## Telegram commands
| Command | What it does |
|---|---|
| `/post <url> [note]` | Write a blog for this job link now. Skips the filter and the daily cap. |
| `/index <url>` | Tell Google (Indexing API) and Bing (IndexNow) about a post you published by hand |
| `/run` | Check sources for new leads now |
| `/backfill` | Search older central/Karnataka govt recruitments that are still open and not yet covered |
| `/status` | Today's counts and the queue |
| `/pending` | Re-send leads and drafts waiting for you |

## Tuning
- **Sources:** `sources.json`. `rss` feeds, or `page` (any careers or notification page; new links are detected and the first scan only records a baseline). Set `"enabled": false` to pause one.
- **Behaviour:** `config.json`. Settings are `max_posts_per_day`, `require_approval`, `min_score`, `filter_model`, `writer_model`, `homepage_cards`, and `claude_extra_args` (extra CLI flags passed to `claude`).
- **Style rules:** `prompts/writer.md`. The writer also copies the 3 newest posts in `blogs/`, so improving your manual posts improves the auto ones.
- **Schedule:** `crontab -e`. Times are in UTC; 02:30 UTC is 08:00 IST.

## Logs and troubleshooting
```bash
tail -f automation/state/automation.log      # everything
tail -f automation/state/cron.log            # cron runs
journalctl -u getupdated-bot -f              # bot service
```
- **"usage limit reached":** the Claude plan limit was hit. The job stays queued and retries on the next run.
- **Publish failed / repo dirty:** run `cd ~/GetUpdated && git status`. Fix it or `git stash`, then tap `/pending`.
- **Stale lock after a crash:** delete `automation/state/*.lock`. Locks also expire automatically.

## Keep Oracle free
Stay within the Always Free A1 shape (4 OCPU / 24 GB) and 200 GB of total block storage. In
Billing, set a budget alert of ₹1 so you're warned if anything starts costing money.

## Google Indexing API (optional, free)
Gets new posts into Google within hours instead of days. 200 URLs a day are free.
1. In console.cloud.google.com, create a project and enable the **Web Search Indexing API**.
2. Go to **IAM & Admin → Service accounts → Create**. No roles are needed. Open the account, then **Keys → Add key → JSON**.
3. In Search Console (property `https://www.getupdated.in/`), go to **Settings → Users and permissions → Add user**. Paste the
   service account email and set it as **Owner**.
4. Copy the JSON to the VM: `scp key.json ubuntu@VM:~/GetUpdated/automation/google-indexing.json`,
   then `chmod 600` it. Each publish then reports `Google: submitted ✅`.
5. Sitemap auto-submit: in the same Cloud project also enable the **Google Search Console API**. Each publish (and
   `/index`) then re-submits `sitemap.xml` and reports `Sitemap: submitted to Google ✅`. If your Search Console
   property is a Domain property, set `"search_console_property": "sc-domain:getupdated.in"` in `config.json`.
   (Google retired the old sitemap "ping" URL in 2023, so this API is the only automatic way.)

## LinkedIn Easy Apply jobs
A LinkedIn posting made by the employer itself counts as the official source, even when the job exists only on
LinkedIn (Easy Apply). The writer reads that one posting through LinkedIn's public guest view, because the normal page
is a login wall from the VM, and links the post to the LinkedIn job.
