"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

Object.defineProperty(exports, "__esModule", {
  value: true
});
exports.stringWrap = stringWrap;
exports.JsonStringify = JsonStringify;
exports.createVertex = createVertex;
exports.createEdge = createEdge;

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _slicedToArray2 = _interopRequireDefault(require("@babel/runtime/helpers/slicedToArray"));

var _Flavors = _interopRequireDefault(require("../config/Flavors"));

function stringWrap(valstr, flavor) {
  var valueWrapped = JSON.stringify(valstr);

  if (flavor === _Flavors["default"].AGENS) {
    valueWrapped = '\'' + valueWrapped.substring(1, valueWrapped.length - 1) + '\'';
  }

  return valueWrapped;
}

function JsonStringify(flavor, record) {
  var ageJsonStr = '{';
  var isFirst = true;

  for (var _i = 0, _Object$entries = Object.entries(record); _i < _Object$entries.length; _i++) {
    var _Object$entries$_i = (0, _slicedToArray2["default"])(_Object$entries[_i], 2),
        key = _Object$entries$_i[0],
        value = _Object$entries$_i[1];

    if (!isFirst) {
      ageJsonStr = ageJsonStr + ',';
    }

    var valueWrapped = stringWrap(value, flavor);
    ageJsonStr = ageJsonStr + "".concat(key, ":").concat(valueWrapped);
    isFirst = false;
  }

  ageJsonStr = ageJsonStr + '}';
  return ageJsonStr;
}

function createVertex(_x, _x2, _x3, _x4, _x5) {
  return _createVertex.apply(this, arguments);
}

function _createVertex() {
  _createVertex = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee(client, graphPathStr, label, record, flavor) {
    var createQ;
    return _regenerator["default"].wrap(function _callee$(_context) {
      while (1) {
        switch (_context.prev = _context.next) {
          case 0:
            createQ = "CREATE (n:".concat(label, " ").concat(JsonStringify(flavor, record), ")");

            if (!(flavor === 'AGE')) {
              _context.next = 5;
              break;
            }

            return _context.abrupt("return", AGECreateVertex(client, graphPathStr, createQ));

          case 5:
            return _context.abrupt("return", AgensGraphCreateVertex(client, graphPathStr, createQ));

          case 6:
          case "end":
            return _context.stop();
        }
      }
    }, _callee);
  }));
  return _createVertex.apply(this, arguments);
}

function AgensGraphCreateVertex(_x6, _x7, _x8) {
  return _AgensGraphCreateVertex.apply(this, arguments);
}

function _AgensGraphCreateVertex() {
  _AgensGraphCreateVertex = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee2(client, graphPathStr, createQ) {
    return _regenerator["default"].wrap(function _callee2$(_context2) {
      while (1) {
        switch (_context2.prev = _context2.next) {
          case 0:
            _context2.next = 2;
            return client.query(createQ);

          case 2:
          case "end":
            return _context2.stop();
        }
      }
    }, _callee2);
  }));
  return _AgensGraphCreateVertex.apply(this, arguments);
}

function AGECreateVertex(_x9, _x10, _x11) {
  return _AGECreateVertex.apply(this, arguments);
}

function _AGECreateVertex() {
  _AGECreateVertex = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee3(client, graphPathStr, createQ) {
    return _regenerator["default"].wrap(function _callee3$(_context3) {
      while (1) {
        switch (_context3.prev = _context3.next) {
          case 0:
            _context3.next = 2;
            return client.query("select *\n         from cypher('".concat(graphPathStr, "', $$ ").concat(createQ, " $$) as (a agtype)"));

          case 2:
          case "end":
            return _context3.stop();
        }
      }
    }, _callee3);
  }));
  return _AGECreateVertex.apply(this, arguments);
}

function createEdge(_x12, _x13, _x14, _x15, _x16, _x17, _x18, _x19, _x20) {
  return _createEdge.apply(this, arguments);
}

function _createEdge() {
  _createEdge = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee4(client, label, record, graphPathStr, edgeStartLabel, edgeEndLabel, startNodeName, endNodeName, flavor) {
    var createQ;
    return _regenerator["default"].wrap(function _callee4$(_context4) {
      while (1) {
        switch (_context4.prev = _context4.next) {
          case 0:
            createQ = "CREATE (:".concat(edgeStartLabel, " {name: ").concat(stringWrap(startNodeName, flavor), "})-[n:").concat(label, " ").concat(JsonStringify(flavor, record), "]->(:").concat(edgeEndLabel, " {name: ").concat(stringWrap(endNodeName, flavor), "})");

            if (!(flavor === 'AGE')) {
              _context4.next = 5;
              break;
            }

            return _context4.abrupt("return", AGECreateEdge(client, graphPathStr, createQ));

          case 5:
            return _context4.abrupt("return", AgensGraphCreateEdge(client, graphPathStr, createQ));

          case 6:
          case "end":
            return _context4.stop();
        }
      }
    }, _callee4);
  }));
  return _createEdge.apply(this, arguments);
}

function AgensGraphCreateEdge(_x21, _x22, _x23) {
  return _AgensGraphCreateEdge.apply(this, arguments);
}

function _AgensGraphCreateEdge() {
  _AgensGraphCreateEdge = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee5(client, graphPathStr, createQ) {
    return _regenerator["default"].wrap(function _callee5$(_context5) {
      while (1) {
        switch (_context5.prev = _context5.next) {
          case 0:
            _context5.next = 2;
            return client.query(createQ);

          case 2:
          case "end":
            return _context5.stop();
        }
      }
    }, _callee5);
  }));
  return _AgensGraphCreateEdge.apply(this, arguments);
}

function AGECreateEdge(_x24, _x25, _x26) {
  return _AGECreateEdge.apply(this, arguments);
}

function _AGECreateEdge() {
  _AGECreateEdge = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee6(client, graphPathStr, createQ) {
    return _regenerator["default"].wrap(function _callee6$(_context6) {
      while (1) {
        switch (_context6.prev = _context6.next) {
          case 0:
            _context6.next = 2;
            return client.query("select *\n         from cypher('".concat(graphPathStr, "', $$ ").concat(createQ, " $$) as (a agtype)"));

          case 2:
          case "end":
            return _context6.stop();
        }
      }
    }, _callee6);
  }));
  return _AGECreateEdge.apply(this, arguments);
}
//# sourceMappingURL=JsonBuilder.js.map