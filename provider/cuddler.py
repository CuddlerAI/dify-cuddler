"""MIT: native provider with bounded credential validation."""
from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from cuddler_dify.client import Client, PlatformError


class CuddlerProvider(ToolProvider):
    def _validate_credentials(self, credentials):
        try:
            Client(credentials.get("api_key")).validate_credentials()
        except PlatformError as error:
            raise ToolProviderCredentialValidationError(error.code) from None
        except Exception:
            raise ToolProviderCredentialValidationError("credential_validation_failed") from None
