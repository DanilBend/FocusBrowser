// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#include "chrome/browser/focus_block/focus_block_ghostery_engine.h"

#include <algorithm>
#include <cstring>
#include <memory>
#include <string_view>
#include <utility>

#include "base/check.h"
#include "base/logging.h"
#include "base/task/single_thread_task_runner.h"
#include "gin/converter.h"
#include "gin/public/isolate_holder.h"
#include "v8/include/v8-array-buffer.h"
#include "v8/include/v8-context.h"
#include "v8/include/v8-exception.h"
#include "v8/include/v8-function.h"
#include "v8/include/v8-isolate.h"
#include "v8/include/v8-local-handle.h"
#include "v8/include/v8-object.h"
#include "v8/include/v8-primitive.h"
#include "v8/include/v8-script.h"
#include "v8/include/v8-typed-array.h"

namespace focus_block {
namespace {

constexpr char kApiName[] = "FocusGhosteryAdblocker";

// A bare browser-process V8 context deliberately has no Web Platform text
// codecs. Ghostery uses TextEncoder/TextDecoder for its deterministic binary
// storage, so expose two tiny native UTF-8 primitives and wrap them in the
// standard surface expected by the upstream bundle.
constexpr char kTextCodecBootstrap[] = R"JS(
(() => {
  class FocusNativeTextEncoder {
    get encoding() { return 'utf-8'; }
    encode(input = '') {
      return globalThis.__focusGhosteryEncodeUtf8(String(input));
    }
    encodeInto(input, destination) {
      const source = String(input);
      const encoded = globalThis.__focusGhosteryEncodeUtf8(source);
      const written = Math.min(encoded.length, destination.length);
      destination.set(encoded.subarray(0, written));
      return { read: written === encoded.length ? source.length : 0, written };
    }
  }
  class FocusNativeTextDecoder {
    constructor() {}
    get encoding() { return 'utf-8'; }
    decode(input = new Uint8Array()) {
      return globalThis.__focusGhosteryDecodeUtf8(input);
    }
  }
  globalThis.TextEncoder = FocusNativeTextEncoder;
  globalThis.TextDecoder = FocusNativeTextDecoder;
})();
)JS";

v8::Local<v8::String> V8String(v8::Isolate* isolate,
                               std::string_view value) {
  return gin::StringToV8(isolate, value);
}

std::string ExceptionText(v8::Isolate* isolate,
                          const v8::TryCatch& try_catch) {
  if (try_catch.Exception().IsEmpty()) {
    return "unknown V8 exception";
  }
  return gin::V8ToString(isolate, try_catch.Exception());
}

bool SetString(v8::Local<v8::Context> context,
               v8::Local<v8::Object> object,
               std::string_view key,
               std::string_view value) {
  v8::Isolate* isolate = v8::Isolate::GetCurrent();
  return object
      ->CreateDataProperty(context, V8String(isolate, key),
                           V8String(isolate, value))
      .FromMaybe(false);
}

bool GetBool(v8::Local<v8::Context> context,
             v8::Local<v8::Object> object,
             std::string_view key,
             bool* value) {
  v8::Local<v8::Value> property;
  v8::Isolate* isolate = v8::Isolate::GetCurrent();
  if (!object->Get(context, V8String(isolate, key))
           .ToLocal(&property) ||
      !property->IsBoolean()) {
    return false;
  }
  *value = property.As<v8::Boolean>()->Value();
  return true;
}

bool GetOptionalString(v8::Local<v8::Context> context,
                       v8::Local<v8::Object> object,
                       std::string_view key,
                       std::string* value) {
  v8::Local<v8::Value> property;
  v8::Isolate* isolate = v8::Isolate::GetCurrent();
  if (!object->Get(context, V8String(isolate, key))
           .ToLocal(&property)) {
    return false;
  }
  if (property->IsNullOrUndefined()) {
    value->clear();
    return true;
  }
  if (!property->IsString()) {
    return false;
  }
  *value = gin::V8ToString(isolate, property);
  return true;
}

}  // namespace

class FocusBlockGhosteryEngine::Impl {
 public:
  Impl(std::string bundle_source, std::string filter_text) {
    if (!gin::IsolateHolder::Initialized()) {
      LOG(ERROR) << "FocusBlock cannot create the Ghostery V8 isolate before "
                    "Gin initialization";
      return;
    }

    isolate_holder_ = std::make_unique<gin::IsolateHolder>(
        base::SingleThreadTaskRunner::GetCurrentDefault(),
        gin::IsolateHolder::IsolateType::kUtility);
    v8::Isolate* isolate = isolate_holder_->isolate();
    isolate->SetMicrotasksPolicy(v8::MicrotasksPolicy::kExplicit);
    v8::Isolate::Scope isolate_scope(isolate);
    v8::HandleScope handle_scope(isolate);

    v8::Local<v8::Context> context = v8::Context::New(isolate);
    context_.Reset(isolate, context);
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Object> global = context->Global();
    global
        ->CreateDataProperty(
            context, V8String(isolate, "__focusGhosteryEncodeUtf8"),
            v8::Function::New(context, &EncodeUtf8,
                              v8::Local<v8::Value>(), 1,
                              v8::ConstructorBehavior::kThrow)
                .ToLocalChecked())
        .Check();
    global
        ->CreateDataProperty(
            context, V8String(isolate, "__focusGhosteryDecodeUtf8"),
            v8::Function::New(context, &DecodeUtf8,
                              v8::Local<v8::Value>(), 1,
                              v8::ConstructorBehavior::kThrow)
                .ToLocalChecked())
        .Check();

    if (!RunScript(context, kTextCodecBootstrap,
                   "focus_ghostery_text_codecs.js") ||
        !RunScript(context, bundle_source,
                   "focus_ghostery_adblocker.js")) {
      return;
    }

    v8::Local<v8::Value> api_value;
    if (!global->Get(context, V8String(isolate, kApiName))
             .ToLocal(&api_value) ||
        !api_value->IsObject()) {
      LOG(ERROR) << "FocusBlock Ghostery bundle did not expose " << kApiName;
      return;
    }
    api_.Reset(isolate, api_value.As<v8::Object>());

    v8::Local<v8::Value> filter_arg = V8String(isolate, filter_text);
    v8::Local<v8::Value> initialize_result;
    if (!Call(context, "initializeFromFilterText", 1, &filter_arg,
              &initialize_result)) {
      return;
    }

    // The checked-in bundle is fixed and does not require eval/new Function
    // after initialization. Disable dynamic code in this private context.
    context->AllowCodeGenerationFromStrings(false);
    ready_ = true;
  }

  ~Impl() {
    if (!isolate_holder_) {
      return;
    }
    v8::Isolate* isolate = isolate_holder_->isolate();
    v8::Isolate::Scope isolate_scope(isolate);
    v8::HandleScope handle_scope(isolate);
    api_.Reset();
    context_.Reset();
  }

  bool ready() const { return ready_; }

  GhosteryMatchResult Match(const GhosteryMatchRequest& request) {
    GhosteryMatchResult result;
    if (!ready_) {
      result.error = "Ghostery engine is not initialized";
      return result;
    }

    v8::Isolate* isolate = isolate_holder_->isolate();
    v8::Isolate::Scope isolate_scope(isolate);
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = context_.Get(isolate);
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Object> details = v8::Object::New(isolate);
    if (!SetString(context, details, "url", request.url) ||
        !SetString(context, details, "sourceUrl", request.source_url) ||
        !SetString(context, details, "type", request.type)) {
      result.error = "failed to build Ghostery request details";
      return result;
    }
    v8::Local<v8::Value> argument = details;
    v8::Local<v8::Value> value;
    if (!Call(context, "matchRawDetails", 1, &argument, &value) ||
        !value->IsObject()) {
      result.error = "Ghostery matchRawDetails returned no object";
      return result;
    }
    v8::Local<v8::Object> object = value.As<v8::Object>();
    bool has_exception = false;
    if (!GetBool(context, object, "validInput", &result.valid_input) ||
        !GetBool(context, object, "matched", &result.matched) ||
        !GetBool(context, object, "hasException", &has_exception) ||
        !GetOptionalString(context, object, "error", &result.error)) {
      result = GhosteryMatchResult();
      result.error = "invalid Ghostery match result";
      return result;
    }
    result.matched = result.valid_input && result.matched && !has_exception;
    return result;
  }

  GhosteryCosmeticResult GetCosmetics(
      const GhosteryCosmeticRequest& request) {
    GhosteryCosmeticResult result;
    if (!ready_) {
      result.error = "Ghostery engine is not initialized";
      return result;
    }

    v8::Isolate* isolate = isolate_holder_->isolate();
    v8::Isolate::Scope isolate_scope(isolate);
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = context_.Get(isolate);
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Object> details = v8::Object::New(isolate);
    if (!SetString(context, details, "url", request.url) ||
        !SetString(context, details, "type", "document") ||
        !SetStringArray(context, details, "classes", request.classes) ||
        !SetStringArray(context, details, "ids", request.ids)) {
      result.error = "failed to build Ghostery cosmetic request";
      return result;
    }
    v8::Local<v8::Value> argument = details;
    v8::Local<v8::Value> value;
    if (!Call(context, "cosmeticsRawDetails", 1, &argument, &value) ||
        !value->IsObject()) {
      result.error = "Ghostery cosmeticsRawDetails returned no object";
      return result;
    }
    v8::Local<v8::Object> object = value.As<v8::Object>();
    if (!GetBool(context, object, "validInput", &result.valid_input) ||
        !GetBool(context, object, "active", &result.active) ||
        !GetOptionalString(context, object, "styles", &result.styles) ||
        !GetOptionalString(context, object, "error", &result.error)) {
      result = GhosteryCosmeticResult();
      result.error = "invalid Ghostery cosmetic result";
    }
    return result;
  }

 private:
  static void EncodeUtf8(
      const v8::FunctionCallbackInfo<v8::Value>& arguments) {
    v8::Isolate* isolate = arguments.GetIsolate();
    std::string value;
    if (arguments.Length() > 0) {
      value = gin::V8ToString(isolate, arguments[0]);
    }
    std::unique_ptr<v8::BackingStore> backing_store =
        v8::ArrayBuffer::NewBackingStore(isolate, value.size());
    if (!value.empty()) {
      std::memcpy(backing_store->Data(), value.data(), value.size());
    }
    v8::Local<v8::ArrayBuffer> buffer =
        v8::ArrayBuffer::New(isolate, std::move(backing_store));
    arguments.GetReturnValue().Set(
        v8::Uint8Array::New(buffer, 0, value.size()));
  }

  static void DecodeUtf8(
      const v8::FunctionCallbackInfo<v8::Value>& arguments) {
    v8::Isolate* isolate = arguments.GetIsolate();
    if (arguments.Length() == 0 || !arguments[0]->IsArrayBufferView()) {
      arguments.GetReturnValue().Set(V8String(isolate, ""));
      return;
    }
    v8::Local<v8::ArrayBufferView> view =
        arguments[0].As<v8::ArrayBufferView>();
    std::shared_ptr<v8::BackingStore> backing_store =
        view->Buffer()->GetBackingStore();
    const char* data = static_cast<const char*>(backing_store->Data()) +
                       view->ByteOffset();
    const size_t size = view->ByteLength();
    v8::Local<v8::String> decoded;
    if (!v8::String::NewFromUtf8(isolate, data,
                                v8::NewStringType::kNormal,
                                static_cast<int>(size))
             .ToLocal(&decoded)) {
      arguments.GetReturnValue().Set(V8String(isolate, ""));
      return;
    }
    arguments.GetReturnValue().Set(decoded);
  }

  bool RunScript(v8::Local<v8::Context> context,
                 std::string_view source,
                 const char* script_name) {
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::TryCatch try_catch(isolate);
    v8::ScriptOrigin origin(V8String(isolate, script_name));
    v8::ScriptCompiler::Source script_source(V8String(isolate, source),
                                              origin);
    v8::Local<v8::Script> script;
    if (!v8::ScriptCompiler::Compile(
             context, &script_source, v8::ScriptCompiler::kNoCompileOptions,
             v8::ScriptCompiler::NoCacheReason::kNoCacheNoReason)
             .ToLocal(&script) ||
        script->Run(context).IsEmpty()) {
      LOG(ERROR) << "FocusBlock failed to evaluate " << script_name << ": "
                 << ExceptionText(isolate, try_catch);
      return false;
    }
    return true;
  }

  bool Call(v8::Local<v8::Context> context,
            std::string_view function_name,
            int argument_count,
            v8::Local<v8::Value>* arguments,
            v8::Local<v8::Value>* result) {
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::TryCatch try_catch(isolate);
    v8::Local<v8::Object> api = api_.Get(isolate);
    v8::Local<v8::Value> function_value;
    if (!api->Get(context, V8String(isolate, function_name))
             .ToLocal(&function_value) ||
        !function_value->IsFunction()) {
      LOG(ERROR) << "FocusBlock Ghostery API is missing " << function_name;
      return false;
    }
    if (!function_value.As<v8::Function>()
             ->Call(context, api, argument_count, arguments)
             .ToLocal(result)) {
      LOG(ERROR) << "FocusBlock Ghostery " << function_name << " failed: "
                 << ExceptionText(isolate, try_catch);
      return false;
    }
    return true;
  }

  bool SetStringArray(v8::Local<v8::Context> context,
                      v8::Local<v8::Object> object,
                      std::string_view key,
                      const std::vector<std::string>& values) {
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::Local<v8::Array> array =
        v8::Array::New(isolate, static_cast<int>(values.size()));
    for (size_t index = 0; index < values.size(); ++index) {
      if (!array
               ->CreateDataProperty(context, static_cast<uint32_t>(index),
                                    V8String(isolate, values[index]))
               .FromMaybe(false)) {
        return false;
      }
    }
    return object
        ->CreateDataProperty(context, V8String(isolate, key), array)
        .FromMaybe(false);
  }

  // Keep the holder declared before persistent handles so explicit teardown
  // and normal reverse member destruction both release handles first.
  std::unique_ptr<gin::IsolateHolder> isolate_holder_;
  v8::Global<v8::Context> context_;
  v8::Global<v8::Object> api_;
  bool ready_ = false;
};

FocusBlockGhosteryEngine::FocusBlockGhosteryEngine(
    std::string bundle_source,
    std::string filter_text)
    : impl_(std::make_unique<Impl>(std::move(bundle_source),
                                   std::move(filter_text))) {}

FocusBlockGhosteryEngine::~FocusBlockGhosteryEngine() = default;

bool FocusBlockGhosteryEngine::IsReady() const {
  return impl_ && impl_->ready();
}

GhosteryMatchResult FocusBlockGhosteryEngine::Match(
    GhosteryMatchRequest request) {
  return impl_ ? impl_->Match(request) : GhosteryMatchResult();
}

GhosteryCosmeticResult FocusBlockGhosteryEngine::GetCosmetics(
    GhosteryCosmeticRequest request) {
  return impl_ ? impl_->GetCosmetics(request) : GhosteryCosmeticResult();
}

}  // namespace focus_block
