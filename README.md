# Kindle Share

把电脑文件夹里的书籍，通过局域网页面下载到 Kindle；电脑端可将 EPUB 转为 MOBI。
电脑管理端提供书籍搜索、格式筛选和转换状态，Kindle 端保留无 JavaScript 的简单下载列表。
下载和断点续传复用 Flask/Werkzeug，服务使用 Waitress，格式转换直接调用 Calibre。

## 环境要求

- Python 3.11 或更新版本，使用 [uv](https://docs.astral.sh/uv/getting-started/installation/) 安装依赖和运行程序。
- 转换 EPUB 时需要安装 [Calibre](https://calibre-ebook.com/download)；只下载现有 MOBI 等文件时不需要。
- 电脑与 Kindle 连接同一局域网，电脑上的服务保持运行。

## 启动

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，在项目目录运行：

```bash
uv sync
uv run python app.py
```

把书籍放进项目的 `books/` 文件夹；也可直接共享已有文件夹（支持子文件夹）：

```bash
uv run python app.py --books "/你的/书籍文件夹"
```

启动后在电脑上打开 `http://127.0.0.1:8080/admin` 管理书籍。管理页仅允许在运行服务的电脑上访问，请使用 `127.0.0.1` 或 `localhost`。

终端和管理页会显示 Kindle 下载地址，例如 `http://192.168.1.10:8080`。
电脑和 Kindle 连接同一个 Wi-Fi，在 Kindle 自带的浏览器中输入这个 **http** 地址，点击书名下面的“下载”，确认下载即可。
电脑需保持开机、服务运行。按 `Ctrl+C` 停止。

| 页面 | 默认访问地址 | 功能 |
| --- | --- | --- |
| 电脑管理端 | `http://127.0.0.1:8080/admin` | 查看文件、搜索书名或路径、按格式筛选、转换 EPUB、查看任务状态、复制 Kindle 地址 |
| Kindle 下载端 | `http://<电脑的局域网 IP>:8080/` | 以黑白大字列表显示可下载文件，刷新列表、点击下载 |

电脑管理端的「全部」「EPUB」「可下载」用于筛选文件；EPUB 筛选也会显示已转换的源文件。管理页中的「预览 Kindle 页面」可在电脑上检查下载列表。

添加或移走书籍后，在 Kindle 页面点“刷新列表”，无需重启服务。
中文文件名会保留在支持它的浏览器中；旧浏览器会使用英文备用文件名。

## 启动参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--books` | 当前工作目录下的 `books/` | 要共享的书籍文件夹，包含子文件夹；指定的文件夹必须存在 |
| `--host` | `0.0.0.0` | 监听所有 IPv4 网络接口；设为 `127.0.0.1` 时仅本机可访问，Kindle 无法连接 |
| `--port` | `8080` | 服务端口，范围为 1–65535 |
| `--ebook-convert` | 自动查找 | 指定 Calibre 的 `ebook-convert` 可执行文件路径 |

例如使用 8081 端口：

```bash
uv run python app.py --books "/你的/书籍文件夹" --port 8081
```

此时电脑管理地址为 `http://127.0.0.1:8081/admin`，Kindle 下载地址也要使用 `8081`。运行 `uv run python app.py --help` 可查看参数。

## EPUB 转 MOBI

1. 安装 [Calibre](https://calibre-ebook.com/download)（本机已安装的可以直接使用）。
2. 将 EPUB 放进共享文件夹，打开电脑管理页面，点击「转为 MOBI」。
3. 页面显示排队、转换中及完成状态；完成后自动刷新书籍列表。
4. 在 Kindle 端刷新列表，下载新增的 MOBI 文件。

转换在电脑后台逐本执行，使用 Calibre 的 `ebook-convert --output-profile kindle --mobi-file-type old`，生成兼容性优先的 MOBI7。EPUB 保留，MOBI 保存到原书旁边，同名文件不覆盖。未完成的文件不会出现在下载列表里。

支持中文名称、空格和子文件夹。同时最多接受 20 个未完成任务，逐本执行；重复点击同一本正在排队或转换的书籍不会重复创建任务。转换失败会在任务中显示原因，每本书最多转换 5 分钟。任务历史保留最近约 100 条，重启后清空，生成的文件保留。不解除 DRM；受保护或损坏的 EPUB 可能转换失败。

程序自动查找 PATH 和 macOS / Linux / Windows 常见安装目录。若未找到，可指定程序路径：

```bash
uv run python app.py --books "/你的/书籍文件夹" --ebook-convert "/Applications/calibre.app/Contents/MacOS/ebook-convert"
```

## 格式与连接

- 显示 `.mobi`、`.azw3`、`.azw`、`.pdf`、`.txt`，大小写不限。
- Kindle 浏览器的下载限制依机型、固件而异。优先用 MOBI；部分机型可直接下载 AZW3、PDF 或 TXT，部分会提示不支持。能阅读某种格式，不等于浏览器允许下载它。
- EPUB 只出现在电脑管理端，Kindle 下载端只显示可阅读的格式。EPUB 转为 MOBI 后会自动加入下载列表。
- 浏览器下载后的入口、书库显示和封面依赖 Kindle 固件，不能保证所有机型都自动出现在书库中。
- 打不开时检查电脑防火墙是否允许 Python/8080，以及 Wi-Fi 是否启用了访客隔离。若终端显示的是 VPN 地址，使用电脑 Wi-Fi 的 IPv4 地址。
- 可用 `--port 8888` 换端口；极旧的 Kindle（1/2/DX）可能需要端口 80，通常需系统权限或路由器端口转发。

服务默认监听 `0.0.0.0:8080`，同网络内可下载该文件夹中列出的书籍；电脑管理及转换接口仅允许本机访问，转换请求有防伪令牌校验。在可信局域网使用，结束后关闭服务。不提供上传或删除接口，不共享隐藏文件或符号链接。

## 常见问题

- **`Address already in use`**：该端口无法绑定。停止正在使用它的服务，或通过 `--port 8081` 换端口，并同步更改浏览器地址。
- **出现 `unrecognized arguments: ─╯` 或多条 `zsh` 报错**：只复制代码块内的命令，不要复制终端提示符、装饰符或程序输出。
- **管理页提示禁止访问**：在运行服务的电脑上使用 `http://127.0.0.1:<端口>/admin`，不要使用局域网 IP 访问管理页。
- **没有出现新增书籍**：检查文件是否在启动时指定的目录内，点击刷新。EPUB 只在电脑管理端显示，转换完成后 Kindle 端才会出现 MOBI。
- **转换按钮不可用**：安装 Calibre 后停止并重新启动服务；使用自定义安装位置时传入 `--ebook-convert`。
- **修改代码后看不到新功能**：先停止旧服务，再重新运行启动命令，并刷新页面。普通书籍文件的新增和移除不需要重启。

## 为什么这样实现

检索发现已有成熟方案 **Calibre Content server `/mobile`**，其官方 FAQ 明确支持 Kindle 的无 JavaScript 浏览器访问；Calibre-Web 的使用者也反馈 `/mobile` 更适合 Kindle。
但它们需要完整书库管理。此项目使用 Flask 的现成文件响应能力，以及 Calibre 现成的转换工具，仅提供传书所需的列表与格式转换。

参考：

- [Calibre FAQ：Kindle 浏览器及 /mobile](https://manual.calibre-ebook.com/faq.html#can-i-access-my-calibre-books-using-the-web-browser-in-my-kindle-or-other-reading-device)
- [Calibre-Web #2723：Kindle 页面兼容性反馈](https://github.com/janeczku/calibre-web/issues/2723)
- [Standard Ebooks：Kindle 浏览器直接下载 AZW3 的操作和限制](https://standardebooks.org/help/how-to-use-our-ebooks)
- [Calibre ebook-convert 命令与 MOBI 输出选项](https://manual.calibre-ebook.com/generated/en/ebook-convert.html)

## 验证

```bash
uv run pytest
uv run ruff check .
```

自动化测试覆盖列表、搜索、筛选、中文文件名、下载字节一致性、子目录、HEAD/Range、越界访问、本机管理限制、转换队列、失败清理、原书保护与重复点击。安装 Calibre 时还会执行真实 EPUB → MOBI 集成测试，未安装时跳过此项。

若在受限执行环境中 Calibre 报 Qt `Incompatible processor ... neon`，请在普通终端运行真实转换测试；不要禁用 Qt 的 CPU 特性。本机正常终端的转换不受此限制。

实际 Kindle 下载仍需在对应机型上验证。

## 开发约定

使用 uv 管理 Python 依赖，通过 `uv run` 运行和测试。新增或调整功能、启动参数、页面入口或使用限制时，同步更新本 README 中对应的说明、命令示例和验证方式。
