import stripe
from app.core.config import settings

# Configure Stripe with the secret key
stripe.api_key = settings.STRIPE_SECRET_KEY


class StripeClient:
    """Thin wrapper around the Stripe SDK for HOA payment flows."""

    # ------------------------------------------------------------------
    # PaymentIntent helpers
    # ------------------------------------------------------------------

    def create_payment_intent(
        self,
        amount_cents: int,
        currency: str = "usd",
        metadata: dict | None = None,
        description: str | None = None,
    ) -> stripe.PaymentIntent:
        """Create a PaymentIntent and return it."""
        return stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency.lower(),
            metadata=metadata or {},
            description=description or "HOA Dues Payment",
            automatic_payment_methods={"enabled": True},
        )

    def retrieve_payment_intent(self, payment_intent_id: str) -> stripe.PaymentIntent:
        """Retrieve a PaymentIntent by ID."""
        return stripe.PaymentIntent.retrieve(payment_intent_id)

    # Alias kept for backwards-compat with existing route code
    def confirm_payment_intent(self, payment_intent_id: str) -> stripe.PaymentIntent:
        return self.retrieve_payment_intent(payment_intent_id)

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> stripe.Event:
        """Verify and parse an incoming Stripe webhook event.

        Raises stripe.error.SignatureVerificationError if the signature
        does not match.
        """
        return stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET,
        )

    # ------------------------------------------------------------------
    # Expose public key so frontend can read it from a /config endpoint
    # ------------------------------------------------------------------

    @property
    def public_key(self) -> str:
        return settings.STRIPE_PUBLIC_KEY


stripe_client = StripeClient()
