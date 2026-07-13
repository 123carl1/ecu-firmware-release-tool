"""仅用于台架内部验证的开发密钥；不能作为量产发布安全边界。"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


DEVELOPMENT_KEY_ID = 1
DEVELOPMENT_PACKAGE_PRIVATE_SEED = bytes.fromhex(
    "4441555f41533550525f4552454c5f4445565f4b45595f323032365f30303031"
)
DEVELOPMENT_PACKAGE_PUBLIC_KEY = bytes.fromhex(
    "57f53989a093bbc31b0956d2fa3847ba6fd8e1aa35001421bdc5c5781d253ae7"
)
DEVELOPMENT_BOOT_HMAC_KEY = bytes.fromhex(
    "4441555f41533550525f4445565f484d41435f4b45595f323032365f30313031"
)


def development_package_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(DEVELOPMENT_PACKAGE_PRIVATE_SEED)
