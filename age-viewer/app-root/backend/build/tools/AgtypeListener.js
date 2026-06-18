"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

Object.defineProperty(exports, "__esModule", {
  value: true
});
exports["default"] = void 0;

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

var _inherits2 = _interopRequireDefault(require("@babel/runtime/helpers/inherits"));

var _possibleConstructorReturn2 = _interopRequireDefault(require("@babel/runtime/helpers/possibleConstructorReturn"));

var _getPrototypeOf2 = _interopRequireDefault(require("@babel/runtime/helpers/getPrototypeOf"));

var _antlr = _interopRequireDefault(require("antlr4"));

function _createSuper(Derived) { var hasNativeReflectConstruct = _isNativeReflectConstruct(); return function _createSuperInternal() { var Super = (0, _getPrototypeOf2["default"])(Derived), result; if (hasNativeReflectConstruct) { var NewTarget = (0, _getPrototypeOf2["default"])(this).constructor; result = Reflect.construct(Super, arguments, NewTarget); } else { result = Super.apply(this, arguments); } return (0, _possibleConstructorReturn2["default"])(this, result); }; }

function _isNativeReflectConstruct() { if (typeof Reflect === "undefined" || !Reflect.construct) return false; if (Reflect.construct.sham) return false; if (typeof Proxy === "function") return true; try { Boolean.prototype.valueOf.call(Reflect.construct(Boolean, [], function () {})); return true; } catch (e) { return false; } }

// This class defines a complete listener for a parse tree produced by AgtypeParser.
var AgtypeListener = /*#__PURE__*/function (_antlr4$tree$ParseTre) {
  (0, _inherits2["default"])(AgtypeListener, _antlr4$tree$ParseTre);

  var _super = _createSuper(AgtypeListener);

  function AgtypeListener() {
    (0, _classCallCheck2["default"])(this, AgtypeListener);
    return _super.apply(this, arguments);
  }

  (0, _createClass2["default"])(AgtypeListener, [{
    key: "enterAgType",
    value: // Enter a parse tree produced by AgtypeParser#agType.
    function enterAgType(ctx) {} // Exit a parse tree produced by AgtypeParser#agType.

  }, {
    key: "exitAgType",
    value: function exitAgType(ctx) {} // Enter a parse tree produced by AgtypeParser#agValue.

  }, {
    key: "enterAgValue",
    value: function enterAgValue(ctx) {} // Exit a parse tree produced by AgtypeParser#agValue.

  }, {
    key: "exitAgValue",
    value: function exitAgValue(ctx) {} // Enter a parse tree produced by AgtypeParser#StringValue.

  }, {
    key: "enterStringValue",
    value: function enterStringValue(ctx) {} // Exit a parse tree produced by AgtypeParser#StringValue.

  }, {
    key: "exitStringValue",
    value: function exitStringValue(ctx) {} // Enter a parse tree produced by AgtypeParser#IntegerValue.

  }, {
    key: "enterIntegerValue",
    value: function enterIntegerValue(ctx) {} // Exit a parse tree produced by AgtypeParser#IntegerValue.

  }, {
    key: "exitIntegerValue",
    value: function exitIntegerValue(ctx) {} // Enter a parse tree produced by AgtypeParser#FloatValue.

  }, {
    key: "enterFloatValue",
    value: function enterFloatValue(ctx) {} // Exit a parse tree produced by AgtypeParser#FloatValue.

  }, {
    key: "exitFloatValue",
    value: function exitFloatValue(ctx) {} // Enter a parse tree produced by AgtypeParser#TrueBoolean.

  }, {
    key: "enterTrueBoolean",
    value: function enterTrueBoolean(ctx) {} // Exit a parse tree produced by AgtypeParser#TrueBoolean.

  }, {
    key: "exitTrueBoolean",
    value: function exitTrueBoolean(ctx) {} // Enter a parse tree produced by AgtypeParser#FalseBoolean.

  }, {
    key: "enterFalseBoolean",
    value: function enterFalseBoolean(ctx) {} // Exit a parse tree produced by AgtypeParser#FalseBoolean.

  }, {
    key: "exitFalseBoolean",
    value: function exitFalseBoolean(ctx) {} // Enter a parse tree produced by AgtypeParser#NullValue.

  }, {
    key: "enterNullValue",
    value: function enterNullValue(ctx) {} // Exit a parse tree produced by AgtypeParser#NullValue.

  }, {
    key: "exitNullValue",
    value: function exitNullValue(ctx) {} // Enter a parse tree produced by AgtypeParser#ObjectValue.

  }, {
    key: "enterObjectValue",
    value: function enterObjectValue(ctx) {} // Exit a parse tree produced by AgtypeParser#ObjectValue.

  }, {
    key: "exitObjectValue",
    value: function exitObjectValue(ctx) {} // Enter a parse tree produced by AgtypeParser#ArrayValue.

  }, {
    key: "enterArrayValue",
    value: function enterArrayValue(ctx) {} // Exit a parse tree produced by AgtypeParser#ArrayValue.

  }, {
    key: "exitArrayValue",
    value: function exitArrayValue(ctx) {} // Enter a parse tree produced by AgtypeParser#obj.

  }, {
    key: "enterObj",
    value: function enterObj(ctx) {} // Exit a parse tree produced by AgtypeParser#obj.

  }, {
    key: "exitObj",
    value: function exitObj(ctx) {} // Enter a parse tree produced by AgtypeParser#pair.

  }, {
    key: "enterPair",
    value: function enterPair(ctx) {} // Exit a parse tree produced by AgtypeParser#pair.

  }, {
    key: "exitPair",
    value: function exitPair(ctx) {} // Enter a parse tree produced by AgtypeParser#array.

  }, {
    key: "enterArray",
    value: function enterArray(ctx) {} // Exit a parse tree produced by AgtypeParser#array.

  }, {
    key: "exitArray",
    value: function exitArray(ctx) {} // Enter a parse tree produced by AgtypeParser#typeAnnotation.

  }, {
    key: "enterTypeAnnotation",
    value: function enterTypeAnnotation(ctx) {} // Exit a parse tree produced by AgtypeParser#typeAnnotation.

  }, {
    key: "exitTypeAnnotation",
    value: function exitTypeAnnotation(ctx) {} // Enter a parse tree produced by AgtypeParser#floatLiteral.

  }, {
    key: "enterFloatLiteral",
    value: function enterFloatLiteral(ctx) {} // Exit a parse tree produced by AgtypeParser#floatLiteral.

  }, {
    key: "exitFloatLiteral",
    value: function exitFloatLiteral(ctx) {}
  }]);
  return AgtypeListener;
}(_antlr["default"].tree.ParseTreeListener);

exports["default"] = AgtypeListener;
//# sourceMappingURL=AgtypeListener.js.map