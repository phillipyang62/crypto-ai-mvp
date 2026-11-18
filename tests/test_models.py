import json
import os
import unittest
from unittest.mock import Mock, patch

from ai_mvp.models import (
    ModelUnavailable,
    OllamaModel,
    OpenAICompatibleModel,
    from_environment,
    remote_from_environment,
)


def connection_returning(data: dict, status: int = 200) -> Mock:
    connection = Mock()
    response = connection.getresponse.return_value
    response.status = status
    response.read.return_value = json.dumps(data).encode("utf-8")
    return connection


class OllamaModelTests(unittest.TestCase):
    def test_requires_an_explicit_local_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(from_environment())
            self.assertEqual(from_environment(model_name="test-model").model, "test-model:latest")
        with patch.dict(os.environ, {"AI_MVP_OLLAMA_MODEL": "environment-model"}, clear=True):
            self.assertEqual(from_environment(model_name="cli-model").model, "cli-model:latest")
        with self.assertRaises(ValueError):
            OllamaModel("test-model", "https://ollama.com")
        with self.assertRaises(ValueError):
            OllamaModel("test-model:cloud")

    def test_refuses_to_send_a_prompt_to_a_remote_alias(self):
        tags = connection_returning({"models": [{
            "model": "local-alias:latest", "remote_host": "https://ollama.com"
        }]})
        with patch("ai_mvp.models.HTTPConnection", return_value=tags):
            with self.assertRaises(ModelUnavailable):
                OllamaModel("local-alias").generate("system", "user")
        tags.request.assert_called_once_with(
            "GET", "/api/tags", body=None, headers={"Accept": "application/json"}
        )

    def test_sends_profile_and_message_as_separate_roles(self):
        tags = connection_returning({"models": [{"model": "local-test:latest"}]})
        reply = connection_returning({
            "message": {"content": "<think>unused</think> 已按画像讲解。"}, "done": True
        })
        with patch("ai_mvp.models.HTTPConnection", side_effect=[tags, reply]):
            result = OllamaModel("local-test").generate("画像", "用户问题")

        self.assertEqual(result, "已按画像讲解。")
        self.assertEqual(reply.request.call_args.args, ("POST", "/api/chat"))
        body = json.loads(reply.request.call_args.kwargs["body"])
        self.assertEqual(body["messages"], [
            {"role": "system", "content": "画像"},
            {"role": "user", "content": "用户问题"},
        ])
        self.assertFalse(body["stream"])

    def test_rejects_redirects_without_opening_another_connection(self):
        redirect = connection_returning({}, status=307)
        with patch("ai_mvp.models.HTTPConnection", return_value=redirect) as connect:
            with self.assertRaisesRegex(ModelUnavailable, "307"):
                OllamaModel("local-test").generate("system", "user")
        connect.assert_called_once_with("127.0.0.1", 11434, timeout=90.0)


class RemoteModelTests(unittest.TestCase):
    def test_key_alone_cannot_enable_remote_requests(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-deepseek-key"}, clear=True):
            self.assertIsNone(remote_from_environment())
        with patch.dict(os.environ, {
            "AI_MVP_REMOTE_ENABLED": "1", "AI_MVP_REMOTE_API_KEY": "previous-provider-key",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
                remote_from_environment()

    def test_key_and_cli_opt_in_use_deepseek_defaults_without_connecting_at_startup(self):
        reply = connection_returning({"choices": [{
            "message": {"content": "按画像回答。"}, "finish_reason": "stop"
        }]})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-deepseek-key"}, clear=True):
            with patch("ai_mvp.models.HTTPSConnection", return_value=reply) as connect:
                model = remote_from_environment(enabled=True)
                self.assertEqual((model.host, model.model), ("api.deepseek.com", "deepseek-flash"))
                connect.assert_not_called()
                self.assertEqual(model.generate("画像", "问题"), "按画像回答。")
        body = json.loads(reply.request.call_args.kwargs["body"])
        connect.assert_called_once_with("api.deepseek.com", 443, timeout=30.0)
        self.assertEqual(reply.request.call_args.args, ("POST", "/v1/chat/completions"))
        self.assertEqual(reply.request.call_args.kwargs["headers"]["Authorization"], "Bearer fake-deepseek-key")
        self.assertEqual(body["max_tokens"], 320)
        self.assertEqual(body["thinking"], {"type": "disabled"})

    def test_custom_provider_requires_an_explicit_model(self):
        with patch.dict(os.environ, {
            "AI_MVP_REMOTE_ENABLED": "1",
            "AI_MVP_REMOTE_API_KEY": "fake-api-key",
            "AI_MVP_REMOTE_BASE_URL": "https://custom-provider.example/v1",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "AI_MVP_REMOTE_MODEL"):
                remote_from_environment()

    def test_default_provider_honors_an_explicit_model_override(self):
        reply = connection_returning({"choices": [{"message": {"content": "回答"}, "finish_reason": "stop"}]})
        with patch.dict(os.environ, {
            "AI_MVP_REMOTE_ENABLED": "1",
            "DEEPSEEK_API_KEY": "fake-deepseek-key",
            "AI_MVP_REMOTE_MODEL": "deepseek-v4-pro",
        }, clear=True):
            with patch("ai_mvp.models.HTTPSConnection", return_value=reply):
                model = remote_from_environment()
                self.assertEqual(model.model, "deepseek-v4-pro")
                model.generate("画像", "问题")
        body = json.loads(reply.request.call_args.kwargs["body"])
        self.assertEqual(body["max_tokens"], 320)
        self.assertEqual(body["thinking"], {"type": "disabled"})

    def test_remote_url_must_be_https_and_must_not_embed_credentials(self):
        for url in ("http://provider.example", "https://private-token@provider.example"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "AI_MVP_REMOTE_BASE_URL") as error:
                    OpenAICompatibleModel("test-model", url, "fake-api-key")
                self.assertNotIn("private-token", str(error.exception))

    def test_single_chat_request_uses_configured_model_and_bearer_header(self):
        reply = connection_returning({"choices": [{
            "message": {"content": " 已按画像讲解。 "}, "finish_reason": "stop"
        }]})
        with patch("ai_mvp.models.HTTPSConnection", return_value=reply) as connect:
            with patch.dict(os.environ, {
                "AI_MVP_REMOTE_ENABLED": "1",
                "AI_MVP_REMOTE_BASE_URL": "https://provider.example/v1/",
                "AI_MVP_REMOTE_MODEL": "test-model",
                "AI_MVP_REMOTE_API_KEY": "fake-api-key",
                "AI_MVP_REMOTE_TIMEOUT_SECONDS": "12",
            }, clear=True):
                model = remote_from_environment()
                connect.assert_not_called()
                self.assertEqual(model.generate("画像", "用户问题"), "已按画像讲解。")

        connect.assert_called_once_with("provider.example", 443, timeout=12.0)
        reply.request.assert_called_once()
        self.assertEqual(reply.request.call_args.args, ("POST", "/v1/chat/completions"))
        self.assertEqual(reply.request.call_args.kwargs["headers"]["Authorization"], "Bearer fake-api-key")
        body = json.loads(reply.request.call_args.kwargs["body"])
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["messages"], [
            {"role": "system", "content": "画像"},
            {"role": "user", "content": "用户问题"},
        ])
        self.assertEqual(body["max_tokens"], 320)
        self.assertFalse(body["stream"])

    def test_http_errors_do_not_read_upstream_body_redirect_or_retry(self):
        model = OpenAICompatibleModel("test-model", "https://provider.example", "fake-api-key")
        for status in (307, 429, 503):
            with self.subTest(status=status):
                reply = connection_returning({"error": "fake-api-key"}, status=status)
                with patch("ai_mvp.models.HTTPSConnection", return_value=reply) as connect:
                    with self.assertRaisesRegex(ModelUnavailable, str(status)) as error:
                        model.generate("profile", "message")
                connect.assert_called_once()
                reply.request.assert_called_once()
                reply.getresponse.return_value.read.assert_not_called()
                self.assertNotIn("fake-api-key", str(error.exception))

    def test_refuses_truncated_or_secret_echoing_responses(self):
        model = OpenAICompatibleModel("test-model", "https://provider.example", "fake-api-key")
        for reason, content in (("length", "truncated"), ("stop", "Echo fake-api-key")):
            with self.subTest(reason=reason):
                reply = connection_returning({"choices": [{
                    "message": {"content": content}, "finish_reason": reason
                }]})
                with patch("ai_mvp.models.HTTPSConnection", return_value=reply):
                    with self.assertRaisesRegex(ModelUnavailable, "no final answer"):
                        model.generate("profile", "message")
