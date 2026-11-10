# EXP Strip

A small overlay for MapleStory Classic that shows your EXP/hour, time to level-up, and a table of your training spots. It works by reading pixels from the screen.

<div dir="rtl">

## עברית

**EXP Strip** היא רצועה קטנה מעל המשחק שמציגה EXP לשעה, זמן עד לעליית רמה, וטבלת מפות אימון.

### הרצה
1. מעתיקים את `ExpStrip.exe` לתיקייה כלשהי ולוחצים עליו פעמיים (אין התקנה, אין צורך בהרשאות מנהל).
2. פותחים את המשחק. הרצועה מופיעה מעליו ומתחילה להתעדכן. אפשר לגרור אותה.
3. המשחק צריך להיות במצב חלון או חלון ללא מסגרת. במחשב שלך גם מסך מלא ב-2560×1440 עובד והרצועה נראית.

### איפה הנתונים
- תיקיית `data\` ליד ה-exe. הקובץ `data\visits.csv` נפתח ישירות באקסל.
- ההגדרות (מיקום הרצועה, נעילה) ב-`settings.json` ליד ה-exe.

### תפריט המגש (ליד השעון)
- **Hide strip / Show strip**: הסתרת הרצועה והחזרתה (גם קליק שמאלי על האייקון). המעקב ממשיך כשהיא מוסתרת.
- **Lock / Unlock**: נעילה (הרצועה "שקופה" ללחיצות) או שחרור.
- **Show in screen share**: הרצועה והטבלה מוסתרות כברירת מחדל מכל שיתוף מסך והקלטה (Discord, OBS). כשמסמנים את האפשרות הזו, מי שצופה בשיתוף רואה אותן. בדיסקורד צריך לשתף **את כל המסך**, לא את חלון המשחק. חשוב: כשהן גלויות בשיתוף, גם התוכנה עצמה רואה אותן, אז לא להניח אותן מעל פס ה-EXP, הקיצורים, שורות האיסוף או שם המפה. אם הן מכסות אחד מהם, תופיע התראה וליד האפשרות בתפריט יופיע ⚠. גם התיק (Inventory) לא צריך להיות מתחת לרצועה כשבודקים מסו.
- **Pause / Resume**: השהיית המעקב.
- **Reset session**: איפוס הסשן.
- **Open data folder**: פתיחת תיקיית הנתונים.
- **Quit**: יציאה.
- הכפתור ☰ ברצועה פותח את טבלת המפות, והכפתור – ממזער אותה למגש.

### שורת המסו
ליד אייקון המטבע ברצועה מופיע המסו נטו של הסשן, בירוק או באדום: **מסו שנאספו + שווי ה-trash + שיקויים שנמצאו − שיקויים ששתית + תיקונים**. התוכנה קוראת את שורות "You have gained mesos (+N)" שמופיעות בפינה הימנית התחתונה, ואת כמות השיקויים בקיצורים. כשפותחים את התיק (Inventory) היא קוראת את סכום המסו האמיתי. הפרשים קטנים (למשל איסוף שפוספס) מתוקנים אוטומטית. הפרשים גדולים (מכירת פריטים, סחר, קניית ציוד) נשארים מחוץ לנטו בכוונה, ונשמרים כ"other" ב-`visits.csv`. ברצועה מופיע המסו נטו ליד אייקון המטבע; מעבר עם העכבר מציג גם מסו לשעה, למשל `Mesos +12.4K · 41K/h`. פריטי "trash" מ-`prices.json` נספרים במחיר המכירה שלהם ברגע האיסוף. שיקוי שנאסף מהרצפה נספר במחיר החנות שלו (`potions`), גם אם הוא לא על מקש קיצור: הוא חוסך קנייה בעתיד. כששותים אותו מהמקש הוא עולה שוב. מכירת ה-trash לא נספרת פעמיים: מונה "trash שלא נמכר" בקובץ `data/loot_tally.json` סופג את המכירה בבדיקת התיק הבאה. ירוק = רווח, אדום = הפסד. ה-Mesos/h מציג "—" בדקה הראשונה במקום.

### מחירים (`data\prices.json`)
הקובץ נוצר אוטומטית בהרצה הראשונה. פותחים אותו בפנקס רשימות, משנים או מוסיפים ושומרים. השינוי נקלט תוך שניות, בלי להפעיל מחדש. אם הקובץ שבור (פסיק חסר וכד'), התוכנה ממשיכה עם המחירים הקודמים. יש בו שני חלקים:
- `potions`: מחיר **הקנייה בחנות** של שיקויים. שיקוי שנאסף נספר במחיר הזה, על מקש קיצור או לא.
- `trash`: מחיר **המכירה ל-NPC** של מה שאוספים ומוכרים.

השם צריך להיות בדיוק כמו במשחק (למשל `"Orange Potion": 150`).

**פריט חדש נלמד לבד.** מוסיפים שם ומחיר לקובץ ומרימים את הפריט פעמיים במשחק. התוכנה קוראת את השם בשורת האיסוף (זיהוי טקסט של Windows, רק שמות שכתובים בקובץ) ושומרת תמונה של השם ב-`data\glyphs\items\`. שיקוי חדש על מקש קיצור: כשמרימים אותו, המספר על המקש עולה, וכך התוכנה מוצאת את המקש ושומרת את האייקון ב-`data\glyphs\potions\` (גם כאן אחרי שתי הרמות). עד שהלמידה מסתיימת, ההרמה הראשונה עלולה לא להיספר. כדי ללמוד מחדש, מוחקים את הקובץ המתאים בתיקיות האלה.

### מסו לשעה
בטבלת המפות יש עמודה **Mesos/hour**. היא מתמלאת אחרי שהביקור במפה נסגר.

### איפוס סשן
כפתור האיפוס ברצועה מאפס את הסשן, אבל צריך **שתי לחיצות** (לחיצה ראשונה מבקשת אישור, השנייה מאפסת) כדי שלא תאפס בטעות. אפשר גם מתפריט המגש: **Reset session**.

### הרמה לא נקראת
אם ברצועה כתוב "Can't read EXP" והמשחק גלוי, ייתכן שהתוכנה לא מכירה עדיין ספרה ברמה שלך. כותבים את הרמה הנוכחית ב-`settings.json` (למשל `"level_hint": 46`) ומפעילים מחדש. התוכנה לומדת את הספרה החסרה ומוחקת את ההגדרה בעצמה אחרי שהשתמשה בה.

### אבחון
`ExpStrip.exe --debug` שומר חיתוכי מסך ל-`data\debug\`. שולחים אותם אם משהו נקרא לא נכון.

### בטיחות
התוכנה **רק קוראת פיקסלים מהמסך**. היא לא נוגעת בתהליך המשחק, בזיכרון שלו, בקלט (מקלדת/עכבר) ולא ברשת. אין שום תקשורת אינטרנט.

</div>

## English

**EXP Strip** is a small strip over the game showing EXP/hour, time to level-up and a table of your training spots.

### Running
1. Copy `ExpStrip.exe` to any folder and double-click it (no installer, no admin rights).
2. Start the game. The strip appears over it and starts updating. Drag it where you like.
3. The game should be windowed or borderless. On the author's setup, full-screen at 2560x1440 also shows the overlay.

### Where data lives
- The `data\` folder next to the exe. `data\visits.csv` opens directly in Excel.
- Settings (strip position, lock) are in `settings.json` next to the exe.

### Tray menu (near the clock)
- **Hide strip / Show strip**: hide the strip and bring it back (a left click on the icon does the same). Tracking keeps running while it is hidden.
- **Lock / Unlock**: make the strip click-through, or movable again.
- **Show in screen share**: the strip and spots table are hidden from every screen share and recording (Discord, OBS) by default. Tick this to let viewers see them. In Discord, share your **whole screen**, not the game window. While they are visible to shares, the app's own capture sees them too, so keep them off the EXP bar, quick slots, pickup lines and map name; if one is covered you get a tray warning and a ⚠ next to the menu item. Keep the open inventory clear of them too.
- **Pause / Resume**: pause tracking.
- **Reset session**
- **Open data folder**
- **Quit**
- The ☰ button on the strip opens the spots table; the – button minimizes it to the tray.

### Meso line
Next to the coin icon on the strip is the session's net mesos, green or red: **mesos picked up + trash value + found potions − potions drunk + corrections**. The app reads the "You have gained mesos (+N)" lines at the bottom right of the screen, and the potion counts on your quickslots. Whenever you open the inventory it also reads your real meso total. Small differences (a missed pickup, say) are corrected automatically. Large ones (selling loot, trading, buying gear) are kept out of the net on purpose and saved as "other" in `visits.csv`. Mesos /h shows "—" for the first minute at a spot. The strip shows the net next to a coin icon; hovering shows the full line, e.g. `Mesos +12.4K · 41K/h`. Trash from `prices.json` (`trash`) is credited at its sell price when it is picked up. A potion found on the floor is credited at its shop price (`potions`), on a quickslot key or not: it saves buying one later. Drinking it from a key costs it again. Selling the trash is not counted twice: an "unsold trash" tally in `data/loot_tally.json` absorbs the sale at the next inventory check. Green = profit, red = loss.

### Prices (`data\prices.json`)
Created automatically on first run. Open it in Notepad, change or add entries and save; it is picked up within a few seconds, no restart. If the file gets broken (a missing comma, say) the app keeps using the last good prices. Two sections:
- `potions`: the **shop price** of potions. A potion you pick up counts at this price, on a key or not.
- `trash`: the **NPC sell price** of loot you pick up and sell.

Names must be spelled as in the game (e.g. `"Orange Potion": 150`).

**New items are learned automatically.** Add the name and price, then pick the item up twice in game. The app reads the name in the pickup line (Windows OCR, accepted only when it equals a name in the file) and saves a name template to `data\glyphs\items\`. For a new potion on a quickslot key, its key count rises when you pick one up; that is how the app finds the key and saves its icon to `data\glyphs\potions\` (also after two pickups). The first pickup may not count while it learns. Delete a file in those folders to make it learn again.

### Mesos/hour
The spots table has a **Mesos/hour** column. It is filled in once the visit to a map has ended.

### Resetting the session
The reset button on the strip restarts the session, but needs **two clicks** (the first asks for confirmation, the second resets) so you cannot do it by accident. The tray menu also has **Reset session**.

### Level not read
If the strip says "Can't read EXP" while the game is visible, the app may not know a digit of your level yet. Put your current level in `settings.json` (e.g. `"level_hint": 46`) and restart. The app learns the missing digit and removes the setting itself once it has used it.

### Troubleshooting
`ExpStrip.exe --debug` saves screen crops to `data\debug\`; send those if something is misread. `ExpStrip.exe --selftest` checks the bundled files and OCR and writes the result to `data\selftest.txt`.

### Safety
It only reads pixels from the screen. It never touches the game process, its memory, keyboard or mouse input, or the network.
