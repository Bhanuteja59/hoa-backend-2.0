import stripe
from app.core.config import settings

stripe.api_key = settings.STRIPE_API_KEY

class StripeClient:
    def create_payment_intent(self, amount_cents: int, currency: str = "usd", metadata: dict = None):
        try:
            intent = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency=currency,
                metadata=metadata,
                automatic_payment_methods={
                    'enabled': True,
                },
            )
            return intent
        except Exception as e:
            raise e

    def confirm_payment_intent(self, payment_intent_id: str):
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            return intent
        except Exception as e:
            raise e
