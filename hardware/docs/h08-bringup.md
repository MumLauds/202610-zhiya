# H08 Windows 构建与验收

状态为 **NOT_TESTED**（ESP32 Wi-Fi、TLS、触摸并发与运行期内存）。正式设备服务为 **BLOCKED_BY_BACKEND**。测试限自有或获准的私有 2.4 GHz 局域网和本地 DEMO 数据。

## 构建

在 ESP-IDF v5.5.5 PowerShell 中执行。SDK 下载 LVGL 9.2.0，H08 使用独立 build 和 sdkconfig。运行本地测试工具时使用带 `cryptography` 的 ESP-IDF Python。Fixture 检查和原 H05 server 只用标准库。

```powershell
$ws = 'D:\zhiya-hardware-workspace'
$repo = Join-Path $ws 'worktrees\zhiya-h08'
$root = Join-Path $ws ('temp\h08-' + [Guid]::NewGuid().ToString('N'))
$build = Join-Path $root 'diagnostics\build'
$config = Join-Path $root 'diagnostics\sdkconfig'
Set-Location "$repo\hardware\firmware\diagnostics\h08"
idf.py --version
idf.py -B $build -D "SDKCONFIG=$config" -D IDF_TARGET=esp32s3 build
if ($LASTEXITCODE -ne 0) { throw 'H08 构建失败' }
```

带本地 CA 的诊断构建在下方 TLS 步骤创建证书后，把 `-D "ZHIYA_H08_DEMO_CA=$lab\ca.pem"` 传给同一条构建命令。该选项只读取公开 CA 证书。当前本机构建产物目录为 `D:\zhiya-hardware-workspace\temp\h08-pr99-r2-mwyyfhth\h08\build`，配置为同级 `sdkconfig`。

2026-11-27 的 ESP-IDF v5.5.5 最终构建结果

| 模式 | `idf.py build` | 应用镜像 | 产物目录 |
| --- | ---: | ---: | --- |
| H02 基线 | 0 | 462,976 B | `D:\zhiya-hardware-workspace\temp\h08-pr99-r2-mwyyfhth\h02\build` |
| H08 本地 CA 诊断 | 0 | 1,265,008 B | `D:\zhiya-hardware-workspace\temp\h08-pr99-r2-mwyyfhth\h08\build` |

两个镜像通过 esptool 校验，分区表二进制相同。H08 应用分区剩余约 70%。实际堆余量受 Wi-Fi、TLS、LCD 与任务分配影响，待实板记录。Windows 工具链的构建并发可用 `$env:IDF_PY_BUILD_JOBS='4'` 设置。

## 第一步 · 烧录独立诊断

接入 4.3C，确认 Windows 新出现的 USB Serial/JTAG COM 口。退出现有串口监视器。

```powershell
$py = 'D:\zhiya-h01-idf-tools\python_env\idf5.5_py3.13_env\Scripts\python.exe'
Set-Location 'D:\zhiya-hardware-workspace\temp\h08-pr99-r2-mwyyfhth\h08\build'
[System.IO.Ports.SerialPort]::GetPortNames()
$port = Read-Host '输入开发板新出现的 COM 口'
& $py -m esptool --chip esp32s3 -p $port -b 460800 --before default_reset --after hard_reset write_flash '@flash_args'
if ($LASTEXITCODE -ne 0) { throw 'H08 烧录失败' }
& $py -m esp_idf_monitor -p $port -b 115200 .\zhiya_h08.elf
```

预期看到 H01 Flash/PSRAM 自检、H02 触摸页和串口 `[H08 DEMO] Ready`。输入 `status` 确认串口命令有效。输入在设备侧无回显，诊断输出不含密码。通过 Ctrl+] 关闭监视器。

## HTTP DEMO

从仓库根目录查询 PC 的活动局域网 IPv4。由硬件负责人确认该接口可与板卡互通、没有 AP 隔离，并在必要时仅针对该接口和测试网段批准防火墙规则。H05 默认仍监听 `127.0.0.1:8765`。

```powershell
Set-Location 'D:\zhiya-hardware-workspace\worktrees\zhiya-h08'
Get-NetIPAddress -AddressFamily IPv4 | Where-Object AddressState -eq Preferred | Select-Object InterfaceAlias,IPAddress
$ip = Read-Host '经确认的 PC 局域网私有 IPv4'
python hardware/mock/server.py --host $ip --port 8765
```

串口输入 `join`，按提示分别输入可信 AP 的 SSID、密码。输入 `status`，等待 `wifi=IP`。将下例的 `PC_IP` 换成实际地址：

```text
shelf http://PC_IP:8765
detail http://PC_IP:8765 2
shelf http://PC_IP:8765 AUTH_REQUIRED
shelf http://PC_IP:8765 FORBIDDEN
shelf http://PC_IP:8765 RESOURCE_NOT_FOUND
shelf http://PC_IP:8765 DEPENDENCY_UNAVAILABLE
```

书架成功预期返回 20 本、首版本 2。详情应为版本 2、6 页、首读页 1。错误注入用 `X-Mock-Error`，结果应为 HTTP 401、403、404、503；可重试 503 的尝试总数至多 2。上述请求只用于 Mock。

## 可信 HTTPS 和延时实验

当前本机诊断镜像内的公开 CA 对应 `D:\zhiya-hardware-workspace\temp\h08-mwyqw6ts\lan-lab-verified`。先核对叶证书的 IP SAN 是否与计划使用的接口匹配。若地址或有效期变化，使用下列命令生成新证书，并用新的 CA 重新构建。叶证书默认 2 天有效。私钥只留在本地测试目录。

```powershell
Set-Location 'D:\zhiya-hardware-workspace\worktrees\zhiya-h08'
$py = 'D:\zhiya-h01-idf-tools\python_env\idf5.5_py3.13_env\Scripts\python.exe'
$ip = Read-Host '已确认且允许监听的 PC 局域网私有 IPv4'
$lab = Join-Path 'D:\zhiya-hardware-workspace\temp' ('h08-certs-' + [Guid]::NewGuid().ToString('N'))
& $py hardware/tests/network/mock_lab.py cert --out $lab --name zhiya-h08.home.arpa --ip $ip
& $py hardware/tests/network/mock_lab.py serve --host $ip --port 8766 --cert "$lab\server.pem" --key "$lab\server.key"
```

等 `status` 显示 `clock=SYNCED`，再发送 `shelf https://PC_IP:8766`。负例使用未被该镜像信任的第二张 CA 签发的证书，或使用同一服务一个可解析但不在 SAN 的别名。验证结果应为 `TLS`。时钟未同步时应为 `CLOCK`。证书或时间错误时，关闭服务后先核对日期、SAN、CA 与镜像来源。

```text
shelf https://PC_IP:8766 H08_SLOW
cancel
create https://PC_IP:8766 2 h08-first-check H08_COMMITTED_504
create https://PC_IP:8766 2 h08-first-check
session https://PC_IP:8766 SESSION_ID
start https://PC_IP:8766 SESSION_ID 0 h08-start-check SESSION_CONFLICT
start https://PC_IP:8766 SESSION_ID 0 h08-start-check IDEMPOTENCY_CONFLICT
start https://PC_IP:8766 SESSION_ID 0 h08-start-check H08_REQUEST_IN_PROGRESS
```

分别测试 `H08_SLOW` 等待超时，以及在请求活动时执行 `cancel`。落库后 504 实验只在内存 Mock 执行，第一条 create 应为 `REQUEST_OUTCOME_UNKNOWN` 且尝试数为 1，第二条用相同请求和键显式诊断重放。将其 DTO 输出中的 ID 代入后续命令。`H08_REQUEST_IN_PROGRESS` 应收到 409 并打印 `retry_after_seconds=2`，尝试数保持 1。正式会话恢复由 H11 处理。

## 验收记录

| 检查 | 预期证据 | 状态 |
| --- | --- | --- |
| 2.4 GHz、密码错误、AP 消失、DHCP 超时 | `status` 的状态、IP、RSSI、原因和重试次数 | NOT_TESTED |
| HTTP Mock 书架与详情 | HTTP 200、request ID、条目和首版本，保留触摸响应 | NOT_TESTED |
| 可信 HTTPS 与负例 | 同步时钟、成功 HTTP 200；错误 CA、主机名、过期证书和不同步时间均拒绝 | NOT_TESTED |
| 错误码、超时、取消 | 观察状态、原始起因、耗时、尝试次数及结果未知 | NOT_TESTED |
| 断网恢复、停止与重入 | 关闭并恢复测试 AP，确认退避与重新取得 IP。连续失败最多 4 次重连。活动请求时执行 `leave`，确认停止后可再次 `join`。 | NOT_TESTED |
| 内存和触摸 | 记录请求前后 PSRAM/internal/DMA free/min/largest、栈余量及触摸响应。建议 20 次混合请求、10 次断网恢复。 | NOT_TESTED |

回退到独立 H02 基线时，退出监视器，在 `D:\zhiya-hardware-workspace\temp\h08-pr99-r2-mwyyfhth\h02\build` 运行相同的 esptool `write_flash '@flash_args'`，该目录的应用为 `zhiya_hardware.bin`。测试后用 Ctrl+C 停止测试端口并撤销本次临时网络放行。

## 主机回归

```powershell
Set-Location 'D:\zhiya-hardware-workspace\worktrees\zhiya-h08'
$cc = 'D:\programmming\Dev-Cpp\MinGW64\bin\gcc.exe'
python hardware/mock/build_fixtures.py --check
python -m unittest discover -s hardware/tests/mock -v
python hardware/tests/board/test_touch.py --cc $cc
& 'D:\zhiya-h01-idf-tools\python_env\idf5.5_py3.13_env\Scripts\python.exe' hardware/tests/network/test_network.py --idf 'D:\zhiya-h01-idf-proxy' --cc $cc
```

网络测试共 7 项。回环实验用主机 TLS 上下文验证正确 CA/SAN、错误 CA、错误主机名和证书过期，再把 HTTP 响应交给设备同款 C 解析器。带 SDK 替身的主机测试直接运行固件 Wi-Fi 和传输代码，覆盖停止事件丢失、队列满、清理失败、DNS 迟到、握手失败、断网与重连、取消及绝对截止时间。ESP32 的无线、TLS 与时序验证仍由实板验收。
