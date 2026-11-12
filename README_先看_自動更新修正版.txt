我的基金雷達｜7 檔基金自動更新修正版（2026/11/13）
====================================================

注意：這是基金專用程式，不含股票報價；不是即時淨值。公開來源的資料日期以網站顯示為準。

【這次修正的問題】
1. 官網資料更新後，直接由 GitHub Actions 發布新的網頁快照。
2. 若 GitHub 無法回存 data/live.json，網頁發布仍可完成，不會卡住。
3. 頁面新增「查看資料更新診斷」，顯示來源成功/失敗與基金配對狀態。
4. 來源抓取失敗、尚未配對與沿用舊淨值，都會分別標示，不顯示假最新價。
5. 保留 7 檔基金；美元基金先顯示、同公司排在一起，南非幣基金最後。

【上傳前先做】
- 不需要刪除 GitHub 儲存庫，不要再重建網址。
- 解壓縮本 ZIP；上傳解壓縮後的內容到既有儲存庫的 main 分支根目錄，不要直接上傳 ZIP。
- index.html、data/live.json、scripts/update_funds.py、tests/test_update_funds.py、.github/workflows/update-and-deploy.yml 都要完整上傳。

【正確上傳步驟】
1. 進入 GitHub > fund-radar > Code > Add file > Upload files。
2. 把 ZIP 解壓縮後的所有檔案與資料夾拖入，然後按 Commit changes。
3. 注意！Windows 可能把 .github 當成隱藏資料夾。確認 GitHub 上有這個檔案：
   .github/workflows/update-and-deploy.yml
   若沒有，請在 GitHub Code > Add file > Create new file，
   將檔名輸入 .github/workflows/update-and-deploy.yml，再把修正版的 yml 內容貼入提交。
4. Settings > Pages > Build and deployment > Source 設成 GitHub Actions。
5. Actions > 我的基金雷達｜自動更新與發布 > Run workflow > main > Run workflow。
6. 等到這個流程顯示綠勾，再開啟你原有的基金網站網址測試。

【3 個快速檢查方法】
A. GitHub > Code 是否存在 .github/workflows/update-and-deploy.yml？
   沒有＝根本沒有自動排程。須補上檔案。
B. GitHub > Actions 是否出現「我的基金雷達｜自動更新與發布」？
   沒有＝流程檔案沒上傳至預設分支或 Actions 未啟用。
C. 在瀏覽器開啟：你原本網站網址 + data/live.json
   例如： https://c87038-droid.github.io/fund-radar/data/live.json
   如果顯示 404＝網頁沒有成功發布 data 目錄；
   如果 checkedAt 是 null＝這還是第一次的空白資料；
   如果 checkedAt 有時間，nav 為 null＝有執行，但來源未取回或基金比對不成功。
   網頁的「查看資料更新診斷」可顯示詳細原因。

【每次更新是什麼意思？】
GitHub Actions 會在台灣時間 09:15、18:15、23:15 嘗試更新，
但 GitHub 可能延遲，資料來源也可能休市或未公告。
頁面上「重新讀取」只是重新讀取上一次成功發布的資料，
不是在手機上直接抓取基金最新淨值。

【說明】
本版已通過程式離線測試；尚未能讀取你的私人 GitHub Actions 執行紀錄，
也未能在此執行環境直接向官方 CSV 實際下載，因此不能保證 7 檔皆已取得最新淨值。
若執行後 7 檔仍「尚無資料」，請進 Actions 點最新一次執行紀錄、打開「更新7檔基金淨值與配息」
或「輸出更新診斷」，截圖錯誤給我，不必再刪掉整個 GitHub 儲存庫。
