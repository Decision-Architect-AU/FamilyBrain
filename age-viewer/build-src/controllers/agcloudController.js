"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _defineProperty2 = _interopRequireDefault(require("@babel/runtime/helpers/defineProperty"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

var _Flavors = _interopRequireDefault(require("../config/Flavors"));

function ownKeys(object, enumerableOnly) { var keys = Object.keys(object); if (Object.getOwnPropertySymbols) { var symbols = Object.getOwnPropertySymbols(object); if (enumerableOnly) { symbols = symbols.filter(function (sym) { return Object.getOwnPropertyDescriptor(object, sym).enumerable; }); } keys.push.apply(keys, symbols); } return keys; }

function _objectSpread(target) { for (var i = 1; i < arguments.length; i++) { var source = arguments[i] != null ? arguments[i] : {}; if (i % 2) { ownKeys(Object(source), true).forEach(function (key) { (0, _defineProperty2["default"])(target, key, source[key]); }); } else if (Object.getOwnPropertyDescriptors) { Object.defineProperties(target, Object.getOwnPropertyDescriptors(source)); } else { ownKeys(Object(source)).forEach(function (key) { Object.defineProperty(target, key, Object.getOwnPropertyDescriptor(source, key)); }); } } return target; }

var sessionService = require('../services/sessionService');

var winston = require('winston');

var logger = winston.createLogger();

var AgcloudController = /*#__PURE__*/function () {
  function AgcloudController() {
    (0, _classCallCheck2["default"])(this, AgcloudController);
  }

  (0, _createClass2["default"])(AgcloudController, [{
    key: "connectDatabase",
    value: function () {
      var _connectDatabase = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee(req, res, next) {
        var databaseService, params;
        return _regenerator["default"].wrap(function _callee$(_context) {
          while (1) {
            switch (_context.prev = _context.next) {
              case 0:
                databaseService = sessionService.get(req.sessionID);

                if (!(databaseService.isConnected() || !req.body)) {
                  _context.next = 5;
                  break;
                }

                res.redirect('/');
                _context.next = 9;
                break;

              case 5:
                params = _objectSpread({
                  flavor: _Flavors["default"].AGENS
                }, req.body);
                _context.next = 8;
                return databaseService.connectDatabase(params);

              case 8:
                res.redirect('/');

              case 9:
              case "end":
                return _context.stop();
            }
          }
        }, _callee);
      }));

      function connectDatabase(_x, _x2, _x3) {
        return _connectDatabase.apply(this, arguments);
      }

      return connectDatabase;
    }()
  }]);
  return AgcloudController;
}();

module.exports = AgcloudController;
//# sourceMappingURL=agcloudController.js.map