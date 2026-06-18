"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

Object.defineProperty(exports, "__esModule", {
  value: true
});
exports["default"] = void 0;

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _objectWithoutProperties2 = _interopRequireDefault(require("@babel/runtime/helpers/objectWithoutProperties"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

var _sessionService = _interopRequireDefault(require("../services/sessionService"));

var _sync = require("csv/lib/sync");

var _JsonBuilder = require("../util/JsonBuilder");

var _excluded = ["start_node", "end_node"];

function _createForOfIteratorHelper(o, allowArrayLike) { var it = typeof Symbol !== "undefined" && o[Symbol.iterator] || o["@@iterator"]; if (!it) { if (Array.isArray(o) || (it = _unsupportedIterableToArray(o)) || allowArrayLike && o && typeof o.length === "number") { if (it) o = it; var i = 0; var F = function F() {}; return { s: F, n: function n() { if (i >= o.length) return { done: true }; return { done: false, value: o[i++] }; }, e: function e(_e) { throw _e; }, f: F }; } throw new TypeError("Invalid attempt to iterate non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method."); } var normalCompletion = true, didErr = false, err; return { s: function s() { it = it.call(o); }, n: function n() { var step = it.next(); normalCompletion = step.done; return step; }, e: function e(_e2) { didErr = true; err = _e2; }, f: function f() { try { if (!normalCompletion && it["return"] != null) it["return"](); } finally { if (didErr) throw err; } } }; }

function _unsupportedIterableToArray(o, minLen) { if (!o) return; if (typeof o === "string") return _arrayLikeToArray(o, minLen); var n = Object.prototype.toString.call(o).slice(8, -1); if (n === "Object" && o.constructor) n = o.constructor.name; if (n === "Map" || n === "Set") return Array.from(o); if (n === "Arguments" || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(n)) return _arrayLikeToArray(o, minLen); }

function _arrayLikeToArray(arr, len) { if (len == null || len > arr.length) len = arr.length; for (var i = 0, arr2 = new Array(len); i < len; i++) { arr2[i] = arr[i]; } return arr2; }

var FeatureController = /*#__PURE__*/function () {
  function FeatureController() {
    (0, _classCallCheck2["default"])(this, FeatureController);
  }

  (0, _createClass2["default"])(FeatureController, [{
    key: "uploadCSV",
    value: function () {
      var _uploadCSV = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee(req, res, next) {
        var connectorService, records, fileName, isVertices, client, _iterator, _step, record, brkStart, brkEnd, ampersand, edgeLabel, edgeStart, edgeEnd, _iterator2, _step2, recordRoot, start_node, end_node, _record;

        return _regenerator["default"].wrap(function _callee$(_context) {
          while (1) {
            switch (_context.prev = _context.next) {
              case 0:
                connectorService = _sessionService["default"].get(req.sessionID);

                if (connectorService.isConnected()) {
                  _context.next = 4;
                  break;
                }

                res.status(500).json({}).end();
                return _context.abrupt("return");

              case 4:
                records = (0, _sync.parse)(req.file.buffer.toString(), {
                  columns: true,
                  skip_empty_lines: true
                });
                fileName = req.file.originalname.substr(0, req.file.originalname.length - 4);
                isVertices = true;

                if (records[0].hasOwnProperty('start_node') && records[0].hasOwnProperty('end_node')) {
                  isVertices = false;
                }

                _context.next = 10;
                return connectorService._agensDatabaseHelper.getConnection();

              case 10:
                client = _context.sent;

                if (!isVertices) {
                  _context.next = 43;
                  break;
                }

                _context.prev = 12;
                _iterator = _createForOfIteratorHelper(records);
                _context.prev = 14;

                _iterator.s();

              case 16:
                if ((_step = _iterator.n()).done) {
                  _context.next = 22;
                  break;
                }

                record = _step.value;
                _context.next = 20;
                return (0, _JsonBuilder.createVertex)(client, connectorService.agensDatabaseHelper._graph, fileName, record, connectorService.agensDatabaseHelper.flavor);

              case 20:
                _context.next = 16;
                break;

              case 22:
                _context.next = 27;
                break;

              case 24:
                _context.prev = 24;
                _context.t0 = _context["catch"](14);

                _iterator.e(_context.t0);

              case 27:
                _context.prev = 27;

                _iterator.f();

                return _context.finish(27);

              case 30:
                _context.next = 36;
                break;

              case 32:
                _context.prev = 32;
                _context.t1 = _context["catch"](12);
                res.status(500).json({}).end();
                return _context.abrupt("return");

              case 36:
                _context.prev = 36;
                _context.next = 39;
                return client.release();

              case 39:
                return _context.finish(36);

              case 40:
                res.status(200).json({}).end();
                _context.next = 79;
                break;

              case 43:
                brkStart = fileName.indexOf('[');
                brkEnd = fileName.indexOf(']');
                ampersand = fileName.indexOf('&');
                edgeLabel = fileName.substring(0, brkStart);
                edgeStart = fileName.substring(brkStart + 1, ampersand);
                edgeEnd = fileName.substring(ampersand + 1, brkEnd);
                _context.prev = 49;
                _iterator2 = _createForOfIteratorHelper(records);
                _context.prev = 51;

                _iterator2.s();

              case 53:
                if ((_step2 = _iterator2.n()).done) {
                  _context.next = 60;
                  break;
                }

                recordRoot = _step2.value;
                start_node = recordRoot.start_node, end_node = recordRoot.end_node, _record = (0, _objectWithoutProperties2["default"])(recordRoot, _excluded);
                _context.next = 58;
                return (0, _JsonBuilder.createEdge)(client, edgeLabel, _record, connectorService.agensDatabaseHelper._graph, edgeStart, edgeEnd, start_node, end_node, connectorService.agensDatabaseHelper.flavor);

              case 58:
                _context.next = 53;
                break;

              case 60:
                _context.next = 65;
                break;

              case 62:
                _context.prev = 62;
                _context.t2 = _context["catch"](51);

                _iterator2.e(_context.t2);

              case 65:
                _context.prev = 65;

                _iterator2.f();

                return _context.finish(65);

              case 68:
                _context.next = 74;
                break;

              case 70:
                _context.prev = 70;
                _context.t3 = _context["catch"](49);
                res.status(500).json({}).end();
                return _context.abrupt("return");

              case 74:
                _context.prev = 74;
                _context.next = 77;
                return client.release();

              case 77:
                return _context.finish(74);

              case 78:
                res.status(200).json({}).end();

              case 79:
              case "end":
                return _context.stop();
            }
          }
        }, _callee, null, [[12, 32, 36, 40], [14, 24, 27, 30], [49, 70, 74, 78], [51, 62, 65, 68]]);
      }));

      function uploadCSV(_x, _x2, _x3) {
        return _uploadCSV.apply(this, arguments);
      }

      return uploadCSV;
    }()
  }]);
  return FeatureController;
}();

var _default = FeatureController;
exports["default"] = _default;
//# sourceMappingURL=FeatureController.js.map