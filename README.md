# GetUpdated - Modular Website Structure

## 📁 Folder Structure

```
Get_Updated/
├── index.html              # Main HTML file (use this for deployment)
├── index-modular.html      # Modular HTML with comments (reference)
├── script.js               # Combined JavaScript (use for simple deployment)
├── styles.css              # Original combined CSS (backup)
│
├── css/                    # 📂 MODULAR CSS FILES
│   ├── base.css           # Variables, reset, common styles
│   ├── components.css     # Preloader, back-to-top, responsive base
│   ├── navbar.css         # Navigation bar styles
│   ├── hero.css           # Hero section styles
│   ├── services.css       # Services section styles
│   ├── news.css           # News/Tech section styles
│   ├── jobs.css           # Jobs section styles
│   ├── income.css         # Income ideas section styles
│   ├── offers.css         # Offers section styles
│   ├── contact.css        # Contact section styles
│   └── footer.css         # Footer styles
│
├── js/                     # 📂 MODULAR JAVASCRIPT FILES
│   ├── main.js            # Main entry point (ES6 modules)
│   ├── navbar.js          # Navigation functionality
│   ├── theme.js           # Dark/light theme toggle
│   ├── hero.js            # Hero animations & stats
│   ├── jobs.js            # Job filtering & bookmarks
│   ├── offers.js          # Countdown timers
│   ├── contact.js         # Contact form handling
│   └── utils.js           # Utility functions
│
└── sections/               # 📂 HTML SECTION TEMPLATES
    ├── navbar.html        # Navigation template
    ├── hero.html          # Hero section template
    ├── services.html      # Services section template
    ├── news.html          # News section template
    ├── jobs.html          # Jobs section template
    ├── income.html        # Income ideas template
    ├── offers.html        # Offers section template
    ├── contact.html       # Contact section template
    └── footer.html        # Footer template
```

## 🔧 How to Update Each Section

### 📰 To Add a New News Article:
1. Open `sections/news.html`
2. Copy an existing `<article class="tech-card">` block
3. Paste and modify the content
4. Styles are in `css/news.css`

### 💼 To Add a New Job Listing:
1. Open `sections/jobs.html`
2. Copy an existing `<div class="job-card">` block
3. Update company logo class, job details, tags
4. Set `data-type` for filtering (remote/fulltime/internship)
5. Styles are in `css/jobs.css`

### 💡 To Add a New Income Idea:
1. Open `sections/income.html`
2. Copy an existing `<div class="income-card">` block
3. Change icon, title, description, earning potential
4. Styles are in `css/income.css`

### 🎁 To Add a New Offer:
1. Open `sections/offers.html`
2. Copy an existing `<div class="offer-card">` block
3. Update badge, discount, image, countdown time
4. Add `hot` class for highlighted deals
5. Styles are in `css/offers.css`

### 🎨 To Change Colors/Theme:
1. Open `css/base.css`
2. Modify the `:root` CSS variables
3. Key variables:
   - `--primary`: Main brand color
   - `--secondary`: Accent color
   - `--bg-primary`: Background color
   - `--text-primary`: Text color

### 📱 To Modify Responsive Breakpoints:
1. Each CSS file has its own responsive styles at the bottom
2. Common breakpoints:
   - 1400px: Large desktop
   - 1024px: Desktop/Laptop
   - 900px: Tablet (hamburger menu appears)
   - 768px: Mobile landscape
   - 576px: Mobile portrait
   - 375px: Small mobile

## 🚀 Deployment Options

### Option 1: Simple (Recommended for beginners)
Use `index.html` with `script.js` - everything works out of the box.

### Option 2: Modular CSS only
Use `index-modular.html` which links to separate CSS files in `/css/` folder.

### Option 3: Full Modular (Advanced)
Use ES6 modules by changing the script tag to:
```html
<script type="module" src="js/main.js"></script>
```
Note: Requires a web server (won't work with file:// protocol)

## 📝 Quick Reference

| Section | HTML File | CSS File | JS File |
|---------|-----------|----------|---------|
| Navbar | sections/navbar.html | css/navbar.css | js/navbar.js |
| Hero | sections/hero.html | css/hero.css | js/hero.js |
| Services | sections/services.html | css/services.css | - |
| News | sections/news.html | css/news.css | - |
| Jobs | sections/jobs.html | css/jobs.css | js/jobs.js |
| Income | sections/income.html | css/income.css | - |
| Offers | sections/offers.html | css/offers.css | js/offers.js |
| Contact | sections/contact.html | css/contact.css | js/contact.js |
| Footer | sections/footer.html | css/footer.css | - |

## 💡 Tips

1. **Adding new sections**: Create new files in `/sections/`, `/css/`, and `/js/` folders
2. **Testing changes**: Always test on multiple screen sizes
3. **Backup**: Keep `styles.css` and `script.js` as backups
4. **Icons**: Uses Font Awesome 6 - browse icons at fontawesome.com
5. **Images**: Using Unsplash for placeholder images - replace with your own

---
Made with ❤️ by GetUpdated Team
