#pragma once

#include <string_view>

namespace credit {

constexpr std::string_view kVersion = "0.1.0";

[[nodiscard]] std::string_view version() noexcept;

}  // namespace credit
