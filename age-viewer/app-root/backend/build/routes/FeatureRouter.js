"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

Object.defineProperty(exports, "__esModule", {
  value: true
});
exports["default"] = void 0;

var _express = require("express");

var _Routes = require("../common/Routes");

var _FeatureController = _interopRequireDefault(require("../controllers/FeatureController"));

var _multer = _interopRequireDefault(require("multer"));

var upload = (0, _multer["default"])({
  storage: _multer["default"].memoryStorage()
});
var featureController = new _FeatureController["default"]();
var router = (0, _express.Router)();
router.post("/uploadCSV", upload.single('file'), (0, _Routes.wrap)(featureController.uploadCSV));
var _default = router;
exports["default"] = _default;
//# sourceMappingURL=FeatureRouter.js.map