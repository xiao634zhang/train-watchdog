# train-watchdog

训练实验看门狗，监控训练进程状态，发现异常时通过多渠道发送告警。

## 功能

- 进程意外退出检测
- GPU 假死/hang 检测（利用率长时间为 0）
- 训练日志错误关键词扫描（OOM、CUDA error、NaN loss 等）
- 编排脚本异常退出检测
- 训练全部完成通知
- 多渠道告警：企业微信 / 飞书 / 钉钉 / 邮件 / 桌面弹窗

## 安装

```bash
pip install requests
```

## 配置

复制模板文件并编辑：

```bash
cp watchdog_config.example.json watchdog_config.json
```

webhook URL 和邮箱密码通过环境变量设置（避免密钥写入文件）：

| 渠道 | 环境变量 |
|------|---------|
| 企业微信 | `WECOM_WEBHOOK_URL` |
| 飞书 | `FEISHU_WEBHOOK_URL` |
| 钉钉 | `DINGTALK_WEBHOOK_URL` |
| 邮件 | `EMAIL_PASSWORD`（163/QQ 邮箱请使用授权码） |

```bash
export WECOM_WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
export EMAIL_PASSWORD="你的SMTP授权码"
```

配置文件中的路径、关键词等可按需调整。

## 用法

```bash
# 默认每 180s 检查一次
python train_watchdog.py

# 自定义检查间隔
python train_watchdog.py --interval 120

# 发送测试通知，验证渠道配置是否正确
python train_watchdog.py --test
```
