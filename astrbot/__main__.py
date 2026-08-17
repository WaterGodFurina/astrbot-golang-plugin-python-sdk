import sys

from astrbot._bridge.server import main

if __name__ == "__main__":
    # 透传 main() 退出码：宿主 go-plugin 依赖进程退出码判断插件启动成败，
    # 恒 0 会把启动失败误判为正常退出。
    sys.exit(main())
