---
description: Automatically push changes to git after making any code updates
---

After completing any file changes or updates in the project, always perform the following steps:

// turbo-all

1. Stage all changes:
```
git add -A
```

2. Commit with a descriptive message summarizing the changes:
```
git commit -m "<descriptive commit message>"
```

3. Push to the remote repository:
```
git push
```

**Notes:**
- Always use a clear, concise commit message that describes what was changed.
- Use semicolons (`;`) instead of `&&` to chain commands in PowerShell.
- Do NOT ask the user before pushing — just push automatically.
- If there are no changes to commit, skip silently.
