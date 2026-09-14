from litellm.integrations.custom_logger import CustomLogger


class HarnessLinkRouteLogger(CustomLogger):
    async def async_post_call_success_deployment_hook(self, request_data, response, call_type):
        return None


proxy_handler_instance = HarnessLinkRouteLogger()
