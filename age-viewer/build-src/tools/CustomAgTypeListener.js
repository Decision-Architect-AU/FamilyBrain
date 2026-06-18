"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

Object.defineProperty(exports, "__esModule", {
  value: true
});
exports["default"] = void 0;

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

var _assertThisInitialized2 = _interopRequireDefault(require("@babel/runtime/helpers/assertThisInitialized"));

var _inherits2 = _interopRequireDefault(require("@babel/runtime/helpers/inherits"));

var _possibleConstructorReturn2 = _interopRequireDefault(require("@babel/runtime/helpers/possibleConstructorReturn"));

var _getPrototypeOf2 = _interopRequireDefault(require("@babel/runtime/helpers/getPrototypeOf"));

var _defineProperty2 = _interopRequireDefault(require("@babel/runtime/helpers/defineProperty"));

var _AgtypeListener2 = _interopRequireDefault(require("./AgtypeListener"));

function _createSuper(Derived) { var hasNativeReflectConstruct = _isNativeReflectConstruct(); return function _createSuperInternal() { var Super = (0, _getPrototypeOf2["default"])(Derived), result; if (hasNativeReflectConstruct) { var NewTarget = (0, _getPrototypeOf2["default"])(this).constructor; result = Reflect.construct(Super, arguments, NewTarget); } else { result = Super.apply(this, arguments); } return (0, _possibleConstructorReturn2["default"])(this, result); }; }

function _isNativeReflectConstruct() { if (typeof Reflect === "undefined" || !Reflect.construct) return false; if (Reflect.construct.sham) return false; if (typeof Proxy === "function") return true; try { Boolean.prototype.valueOf.call(Reflect.construct(Boolean, [], function () {})); return true; } catch (e) { return false; } }

var CustomAgTypeListener = /*#__PURE__*/function (_AgtypeListener) {
  (0, _inherits2["default"])(CustomAgTypeListener, _AgtypeListener);

  var _super = _createSuper(CustomAgTypeListener);

  function CustomAgTypeListener() {
    var _this;

    (0, _classCallCheck2["default"])(this, CustomAgTypeListener);

    for (var _len = arguments.length, args = new Array(_len), _key = 0; _key < _len; _key++) {
      args[_key] = arguments[_key];
    }

    _this = _super.call.apply(_super, [this].concat(args));
    (0, _defineProperty2["default"])((0, _assertThisInitialized2["default"])(_this), "rootObject", null);
    (0, _defineProperty2["default"])((0, _assertThisInitialized2["default"])(_this), "objectInsider", []);
    (0, _defineProperty2["default"])((0, _assertThisInitialized2["default"])(_this), "prevObject", null);
    (0, _defineProperty2["default"])((0, _assertThisInitialized2["default"])(_this), "lastObject", null);
    (0, _defineProperty2["default"])((0, _assertThisInitialized2["default"])(_this), "lastValue", null);
    return _this;
  }

  (0, _createClass2["default"])(CustomAgTypeListener, [{
    key: "mergeArrayOrObject",
    value: function mergeArrayOrObject(key) {
      if (this.prevObject instanceof Array) {
        this.mergeArray();
      } else {
        this.mergeObject(key);
      }
    }
  }, {
    key: "mergeArray",
    value: function mergeArray() {
      this.prevObject.push(this.lastObject);
      this.lastObject = this.prevObject;
      this.objectInsider.shift();
      this.prevObject = this.objectInsider[1];
    }
  }, {
    key: "mergeObject",
    value: function mergeObject(key) {
      this.prevObject[key] = this.lastObject;
      this.lastObject = this.prevObject;
      this.objectInsider.shift();
      this.prevObject = this.objectInsider[1];
    }
  }, {
    key: "createNewObject",
    value: function createNewObject() {
      var newObject = {};
      this.objectInsider.unshift(newObject);
      this.prevObject = this.lastObject;
      this.lastObject = newObject;
    }
  }, {
    key: "createNewArray",
    value: function createNewArray() {
      var newObject = [];
      this.objectInsider.unshift(newObject);
      this.prevObject = this.lastObject;
      this.lastObject = newObject;
    }
  }, {
    key: "pushIfArray",
    value: function pushIfArray(value) {
      if (this.lastObject instanceof Array) {
        this.lastObject.push(value);
        return true;
      }

      return false;
    }
  }, {
    key: "exitStringValue",
    value: function exitStringValue(ctx) {
      var value = this.stripQuotes(ctx.getText());

      if (!this.pushIfArray(value)) {
        this.lastValue = value;
      }
    }
  }, {
    key: "exitIntegerValue",
    value: function exitIntegerValue(ctx) {
      var value = parseInt(ctx.getText());

      if (!this.pushIfArray(value)) {
        this.lastValue = value;
      }
    }
  }, {
    key: "exitFloatValue",
    value: function exitFloatValue(ctx) {
      var value = parseFloat(ctx.getText());

      if (!this.pushIfArray(value)) {
        this.lastValue = value;
      }
    }
  }, {
    key: "exitTrueBoolean",
    value: function exitTrueBoolean(ctx) {
      var value = true;

      if (!this.pushIfArray(value)) {
        this.lastValue = value;
      }
    }
  }, {
    key: "exitFalseBoolean",
    value: function exitFalseBoolean(ctx) {
      var value = false;

      if (!this.pushIfArray(value)) {
        this.lastValue = value;
      }
    }
  }, {
    key: "exitNullValue",
    value: function exitNullValue(ctx) {
      var value = null;

      if (!this.pushIfArray(value)) {
        this.lastValue = value;
      }
    }
  }, {
    key: "exitFloatLiteral",
    value: function exitFloatLiteral(ctx) {
      var value = ctx.getText();

      if (!this.pushIfArray(value)) {
        this.lastValue = value;
      }
    }
  }, {
    key: "enterObjectValue",
    value: function enterObjectValue(ctx) {
      this.createNewObject();
    }
  }, {
    key: "enterArrayValue",
    value: function enterArrayValue(ctx) {
      this.createNewArray();
    }
  }, {
    key: "exitObjectValue",
    value: function exitObjectValue(ctx) {
      if (this.prevObject instanceof Array) {
        this.mergeArray();
      }
    }
  }, {
    key: "exitPair",
    value: function exitPair(ctx) {
      var name = this.stripQuotes(ctx.STRING().getText());

      if (this.lastValue !== undefined) {
        this.lastObject[name] = this.lastValue;
        this.lastValue = undefined;
      } else {
        this.mergeArrayOrObject(name);
      }
    }
  }, {
    key: "exitAgType",
    value: function exitAgType(ctx) {
      this.rootObject = this.objectInsider.shift();
    }
  }, {
    key: "stripQuotes",
    value: function stripQuotes(quotesString) {
      return JSON.parse(quotesString);
    }
  }, {
    key: "getResult",
    value: function getResult() {
      return this.rootObject;
    }
  }]);
  return CustomAgTypeListener;
}(_AgtypeListener2["default"]);

var _default = CustomAgTypeListener;
exports["default"] = _default;
//# sourceMappingURL=CustomAgTypeListener.js.map