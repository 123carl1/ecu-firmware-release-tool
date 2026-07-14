from __future__ import annotations

from unified_can_lin_host_tool.update.runtime_mutex import (
    is_product_mutex_present,
    product_run_mutex,
)


def test_product_mutex_exists_while_context_is_active():
    with product_run_mutex():
        assert is_product_mutex_present() is True
    assert is_product_mutex_present() is False

def test_multiple_process_handles_keep_mutex_present():
    with product_run_mutex():
        with product_run_mutex():
            assert is_product_mutex_present() is True
        assert is_product_mutex_present() is True
    assert is_product_mutex_present() is False
