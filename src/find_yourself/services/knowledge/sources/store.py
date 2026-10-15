"""W3 凭证存储实例（N7 V1-4 解环：本模块下沉记账实例）。

``secret_store``（hub Fernet 加密存储的接口壳）原先直接定义在
``sources/__init__.py`` 里，导致 ``baidu_pan`` 为取它而回指包 ``__init__``，
与 ``__init__ → baidu_pan`` 构成 L2 同层环（V1-4）。下沉到本模块后：

* ``baidu_pan`` / ``connector`` 等取 ``from .store import secret_store``，
  不再回指包 ``__init__``；
* 包 ``__init__`` 从本模块重导出（``__all__`` 不变，所有既有导入方零改动）；
* 本模块只依赖 ``hub.secrets``（L2 内单向边），不构成新环。

接口语义见 ``sources/__init__.py`` 模块 docstring（双模式与诚实标注）。
"""

from __future__ import annotations

from ...hub.secrets import HubSecretStore

#: 默认凭证存储 = hub 的 Fernet 加密存储（接口壳，签名与 InMemorySecretStore 一致）。
secret_store = HubSecretStore()
