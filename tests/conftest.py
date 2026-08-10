"""测试夹具。

签名密钥必须在导入 app.config 之前进入环境——它在模块加载时就被读走了，
这也正是"缺失则拒绝启动"那条约束的副作用。
"""

import os

os.environ.setdefault("STATE_SIGNING_SECRET", "test-secret-not-a-real-key")
