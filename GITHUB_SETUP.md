# 从零开始把周报搬到 GitHub

给完全没用过 GitHub 的人写的。跟着做，大约 20 分钟。

**做完你能得到什么**：每周一早上，GitHub 的服务器自动跑一次，生成周报存进你的仓库。你电脑可以关机，代理也不用开。

---

## 先说一句实话

浏览器打开 `github.com` **在国内经常很慢甚至打不开**，这时候需要开你的代理（Clash）。

注意这和 agent 本身无关 —— **agent 在 GitHub 服务器上跑，不需要你的代理**。这里说的只是"你打开网页去配置"这个动作可能要用代理。

---

## 第 0 步：注册账号

1. 浏览器打开 https://github.com/signup
2. 依次填：邮箱 → 密码 → 用户名
   - **用户名很重要**，它会出现在仓库地址里，只能用英文、数字、连字符
   - 比如 `zhangsan` 或 `zhang-san`
3. 收邮件，点验证链接
4. 登录后，右上角头像 → Settings → 建议开启 **2FA**（两步验证）

> ✅ 检查点：登录后能看到右上角你的头像，说明账号好了。

---

## 第 1 步：创建仓库

1. 右上角 **+** → **New repository**
2. 填写：
   - **Repository name**：`security-ai-weekly`（随便起，英文）
   - **Description**：可不填
   - 选 **🔒 Private** 或 **🌍 Public**——公开的话别人也能读周报；密钥只放 Secrets，跟可见性无关
3. **下面三个勾选框全部不要勾**：
   - ❌ Add a README file
   - ❌ Add .gitignore
   - ❌ Choose a license

   ⚠️ 为什么不能勾？因为我们本地已经有这些文件了，GitHub 再建一份会**冲突**，push 会失败。

4. 点 **Create repository**

> ✅ 检查点：看到一个空仓库页面，上面写着 "Quick setup — if you've done this kind of thing before"。

**留在这一页，等下要用它的地址。**

---

## 第 2 步：把 API Key 存进仓库（Secret）

Key 不能写进代码，要存在 GitHub 的加密变量里。

1. 在仓库页面 → **Settings**（顶部标签栏，不是头像下的 Settings）
2. 左侧栏 → **Secrets and variables** → **Actions**
3. 点绿色按钮 **New repository secret**
4. 填写：
   - **Name**：`DEEPSEEK_API_KEY` ← 必须一字不差，全大写
   - **Secret**：`sk-你的key`
5. 点 **Add secret**

> ✅ 检查点：列表里出现 `DEEPSEEK_API_KEY`，值是隐藏的。

---

## 第 3 步：在电脑上提交代码

打开 PowerShell（Win 键 → 输入 powershell → 回车），逐条执行：

```powershell
cd "D:\deepseek harness\news-agent"

git config user.name "你的GitHub用户名"
git config user.email "你的GitHub邮箱"

git commit -m "初始版本"
```

把 `你的GitHub用户名` 和 `你的GitHub邮箱` 换成你注册时用的（邮箱要和 GitHub 账号一致，这样提交记录才算你的）。

> ✅ 检查点：看到 `27 files changed, ...` 这样一行。

> ℹ️ 我已经帮你把这些文件"暂存"好了（相当于已经把要提交的东西挑出来了），所以你不用自己 `git add`。

---

## 第 4 步：连到 GitHub 并推送

先拿到仓库地址：回到 GitHub 仓库页面，点绿色的 **Code** 按钮 → 复制 HTTPS 地址，长这样：

```
https://github.com/你的用户名/security-ai-weekly.git
```

然后执行（把地址换成你复制的）：

```powershell
git remote add origin https://github.com/你的用户名/security-ai-weekly.git

git push -u origin main
```

### 这时候会发生什么

**正常情况下**：会弹出一个浏览器窗口，让你登录 GitHub → 点 **Authorize** → 回到 PowerShell，看到上传进度，结束。

**这是 Git Credential Manager 在工作**，你电脑上已经装了，不用管。

> ✅ 检查点：看到 `branch 'main' set up to track 'origin/main'`，并且 GitHub 页面刷新后能看到你的文件。

---

## 第 5 步：手动跑一次（别等周一）

1. GitHub 仓库页面 → 顶部 **Actions** 标签
2. 左侧点 **科技周报**
3. 右边有个 **Run workflow** 下拉按钮 → 点它 → 再点绿色的 **Run workflow**
4. 等几秒刷新页面，会出现一条运行记录（黄色转圈）
5. 点进去 → 点 **digest** 这个 job，可以看实时日志

运行时间约 1-3 分钟。

> ⚠️ **如果左侧看不到「科技周报」**：等 1-2 分钟刷新。GitHub 需要先识别到 workflow 文件，有时有延迟。

> ✅ 检查点：运行记录变成 **绿色对勾**。

---

## 第 6 步：看周报

### 在电脑上

仓库页面 → 点进 `reports` 文件夹 → 点 `weekly_2026-10-17.md` → 看渲染好的内容。

### 在手机上

1. 手机应用商店搜 **GitHub**，装官方 App
2. 登录同一个账号
3. 打开 `security-ai-weekly` 仓库 → `reports` 文件夹 → 点开 md 文件

---

## 跑完之后要检查的一件事

**第一次跑完，打开周报拉到最底部，看「附：数据来源」那张表。**

因为 GitHub 的服务器在国外，访问国内站点（先知社区、奇安信、绿盟、量子位…）可能有个别失败。表里会明确写出每个源的状态。

如果发现有源失败，把源名告诉我，我换掉或者加长超时。

---

## 常见错误对照表

### 1. `error: remote origin already exists`

说明之前加过远程地址。改一下就好：

```powershell
git remote set-url origin https://github.com/你的用户名/你的仓库名.git
```

### 2. `Authentication failed` / 一直要密码

GitHub **不接受账号密码**，需要 token。两个办法：

**办法 A（推荐）**：让 Git Credential Manager 重新弹窗

```powershell
git credential-manager configure
git push -u origin main
```

**办法 B**：手动用 Personal Access Token
1. GitHub → 头像 → Settings → 最下面 Developer settings
2. Personal access tokens → Tokens (classic) → Generate new token (classic)
3. 勾选 **repo** 权限 → 生成 → 复制（**只显示一次**）
4. `git push` 时，用户名填你的 GitHub 用户名，密码粘贴这个 token

### 3. `src refspec main does not match any`

分支名不对。执行：

```powershell
git branch -M main
git push -u origin main
```

### 4. Actions 里运行失败，日志说找不到 API Key

Secret 名字拼错了。回第 2 步检查，必须是 `DEEPSEEK_API_KEY`，全大写、下划线。

### 5. 运行失败了，怎么看原因

Actions → 点那条红色的运行 → 点 **digest** → 展开红色叉的那一步，看报错文字。
把报错贴给我，我帮你看。

### 6. 提交时提示 `nothing to commit`

说明代码已经提交过了，直接跳到第 4 步。

---

## 配好之后的日常

| 什么时候 | 发生什么 |
|---|---|
| 每周一北京时间 12:05 | 自动生成（可能延迟十几分钟，正常；这个点是 DeepSeek 的空闲时段，价钱是高峰的一半） |
| 每周一北京时间 13:20 | 自动体检一次：今天的周报已经生成就什么都不做，没生成就补跑一期 |
| 跑完 | 周报提交进仓库，手机上能看 |
| 想让它在别的时候跑 | Actions → Run workflow 手动触发 |

**唯一需要你偶尔做的事**：GitHub 有个规则 —— **仓库连续 60 天没有任何提交，定时任务会被自动禁用**。周报每周都会产生提交，所以正常情况下不会触发。如果哪天发现不动了，去 Actions 页面点一次 Run workflow 就恢复了。

---

## 需要帮忙的话

第 3、4 步是纯本地操作，**我可以直接帮你执行**。你把这两个信息告诉我：

1. 你的 GitHub 用户名
2. 你的 GitHub 邮箱（或者你不介意用哪个邮箱）
3. 仓库名（如果用了 `security-ai-weekly` 就不用说）

我就把 `git config`、`git commit`、`git remote add` 都跑好，你只需要执行最后一条 `git push`（因为推送需要你本人在弹出的窗口里登录）。
