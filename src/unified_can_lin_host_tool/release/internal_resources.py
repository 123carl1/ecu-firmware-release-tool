"""随程序封装的受控二进制资源，不在安装目录单独展开。"""

from __future__ import annotations

import base64
import hashlib
import zlib


_AS5PR_FLASH_DRIVER_B85 = (
    "c-mD`O=uHQ5S~qU^IyA7Kutv1q>?rs#HNZrASSzYgIQ@y4k87?q(zAq3C+P^D6F>{z1TpYQIH~@RJ5s4iq-"
    "}^Tay@}$*I&sBo~o_8%oJ@Hr+!VnEmFxnc?mC@m(1XixdEGm@_PJ3z###z}>fXu7Emg^b7peKrtT=g4b9w9h~J2>vG={-"
    "8|2%QygpnhaGOG0o-"
    "=u_ZBNpzmEsyEp|2@)Jv4x8Kr#AXxpbE!LRJDfHR64Ecn@iLCW3cTxKCRQJcaJV;1U@MEOZ5j$J8=a1{Kl0u9|DyB+{hmI(}4OKL"
    "rUr#zmRk{5q-"
    "w}x_S#WRwKc|vx7B9e&d+!;Yd>YN>sD2v1ZoU{~C(*qNZCH`2AD6h$3Y3Mri9SGTORSb?QH6**kx(sir*wq`xQ0+yWW4fw6h+(Gr"
    "qZ?U;`CQ}-zWa~>_oZR%@I+1Esd!_!+@SYN+(Rz;2692ZWQUL}Mib-"
    "f%agWNVNDE*a)ALW><92d`OXIIBFaT%V=2+D6S#k&!*a00SgpIt#ZSxPTd}ord^8oKkYg%>=(8iL6SC-"
    ")K1S4p{tx{5%BJIc@{s;<(nMQ?aMGr;3!B&M!n}Ay8jn$~v@jk)95f>mrJtl6A<?O+G54PuHcbsyQ)8c|hE?P07l}fn8gEby9jZZ"
    "}UTrXYdVkv#w}2jbrfKm4;}3>Go6$@4Dg7n0zdortB8`rq&{w;&st*VutLn}x9YEmJSjV&a%+2)T-"
    "FLsX*Pf4>0^j1FA59xQ&Dv#pEJw2Kp6oxL=?`c"
)
_AS5PR_FLASH_DRIVER_SHA256 = "95a098b0bf08352bee9cdcc288c768cf6a884a4b432312a71f64d20ba6be7f98"


def load_as5pr_flash_driver() -> bytes:
    try:
        content = zlib.decompress(base64.b85decode(_AS5PR_FLASH_DRIVER_B85))
    except Exception as exc:
        raise RuntimeError("internal FlashDriver resource is corrupt") from exc
    if hashlib.sha256(content).hexdigest() != _AS5PR_FLASH_DRIVER_SHA256:
        raise RuntimeError("internal FlashDriver resource hash mismatch")
    return content
