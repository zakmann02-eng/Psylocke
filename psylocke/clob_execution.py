"""Order placement via Polymarket's CLOB API (py-clob-client).

The client is constructed lazily so that DRY_RUN mode — and all of
Psylocke 2's unit tests — can run without POLY_PRIVATE_KEY ever being set.
Only a live (non-dry-run) order actually needs signing credentials.
"""
import logging

logger = logging.getLogger("psylocke.clob_execution")


class ExecutionClient:
    def __init__(self, config):
        self._config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            from py_clob_client.client import ClobClient

            if not self._config.poly_private_key:
                raise RuntimeError(
                    "POLY_PRIVATE_KEY is required to place live orders"
                )
            client = ClobClient(
                host=self._config.clob_api_base,
                key=self._config.poly_private_key,
                chain_id=self._config.poly_chain_id,
                signature_type=self._config.poly_signature_type,
                funder=self._config.poly_funder_address or None,
            )
            client.set_api_creds(client.create_or_derive_api_creds())
            self._client = client
        return self._client

    def place_order(self, token_id: str, side: str, price: float, size_shares: float):
        """side is 'BUY' or 'SELL'. Returns the CLOB response dict."""
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        client = self._get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size_shares,
            side=BUY if side == "BUY" else SELL,
        )
        signed_order = client.create_order(order_args)
        logger.info("placing %s order token=%s price=%s size=%s", side, token_id, price, size_shares)
        return client.post_order(signed_order, OrderType.GTC)
