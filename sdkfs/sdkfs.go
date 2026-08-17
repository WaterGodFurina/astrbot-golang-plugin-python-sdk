// Package sdkfs 标记 astrbot-golang-plugin-python-sdk 为可被宿主 import 的
// Go 模块，使宿主 go.mod 的 require 保持生效（SDK 本体是 Python 代码，经
// `go list -m` 在运行时解析模块目录，见宿主 internal/pysdk）。
package sdkfs

// ModulePath 是本 SDK 的 Go module 路径。
const ModulePath = "github.com/WaterGodFurina/astrbot-golang-plugin-python-sdk"
