import React, { useCallback, useRef, useMemo } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
import * as THREE from 'three'
import { useStore, labelColor } from '../store'

function nodeLabel(node) {
  const p = node.properties ?? {}
  return p.name ?? p.title ?? p.subject ?? p.filename ?? node.labels?.[0] ?? node.id
}

function buildSphere(color) {
  const mat = new THREE.MeshLambertMaterial({ color })
  return (node) => {
    const geo = new THREE.SphereGeometry(5)
    return new THREE.Mesh(geo, mat.clone())
  }
}

export default function GraphCanvas() {
  const fgRef = useRef()

  const {
    nodes, edges,
    selected, setSelected,
    hiddenLabels, hiddenRelTypes,
    searchText,
  } = useStore()

  const graphData = useMemo(() => {
    const visNodes = nodes
      .filter((n) => !hiddenLabels.has(n.labels?.[0]))
      .filter((n) => {
        if (!searchText) return true
        const q = searchText.toLowerCase()
        return Object.values(n.properties ?? {}).some((v) => String(v).toLowerCase().includes(q))
      })

    const nodeIds = new Set(visNodes.map((n) => n.id))

    const visEdges = edges
      .filter((e) => !hiddenRelTypes.has(e.type))
      .filter((e) => nodeIds.has(e.startNode) && nodeIds.has(e.endNode))

    return {
      nodes: visNodes.map((n) => ({
        id:         n.id,
        label:      nodeLabel(n),
        color:      labelColor(n.labels?.[0]),
        labels:     n.labels,
        properties: n.properties,
        raw:        n,
      })),
      links: visEdges.map((e) => ({
        source:     e.startNode,
        target:     e.endNode,
        type:       e.type,
        id:         e.id,
        properties: e.properties,
        raw:        e,
      })),
    }
  }, [nodes, edges, hiddenLabels, hiddenRelTypes, searchText])

  const handleNodeClick = useCallback((node) => {
    setSelected({ type: 'node', data: node.raw })
    // Fly camera toward clicked node
    if (fgRef.current) {
      const dist = 120
      const { x = 0, y = 0, z = 0 } = node
      fgRef.current.cameraPosition(
        { x: x + dist, y: y + dist, z: z + dist },
        { x, y, z },
        800
      )
    }
  }, [setSelected])

  const handleLinkClick = useCallback((link) => {
    setSelected({ type: 'edge', data: link.raw })
  }, [setSelected])

  const nodeThreeObject = useCallback((node) => {
    const isSelected = selected?.data?.id === node.id
    const isSearchMatch = searchText &&
      Object.values(node.properties ?? {}).some((v) =>
        String(v).toLowerCase().includes(searchText.toLowerCase())
      )

    const geo = new THREE.SphereGeometry(isSelected ? 8 : 5, 16, 16)
    const mat = new THREE.MeshLambertMaterial({
      color: isSearchMatch ? '#FFD94A' : node.color,
      emissive: isSelected ? '#ffffff' : '#000000',
      emissiveIntensity: isSelected ? 0.15 : 0,
    })
    const mesh = new THREE.Mesh(geo, mat)

    // Floating label sprite
    const canvas = document.createElement('canvas')
    canvas.width = 256; canvas.height = 64
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = 'rgba(0,0,0,0)'
    ctx.clearRect(0, 0, 256, 64)
    ctx.font = 'bold 24px Inter, system-ui, sans-serif'
    ctx.fillStyle = '#ffffff'
    ctx.textAlign = 'center'
    ctx.fillText(node.label?.slice(0, 28) ?? '', 128, 40)

    const tex = new THREE.CanvasTexture(canvas)
    const spriteMat = new THREE.SpriteMaterial({ map: tex, depthWrite: false })
    const sprite = new THREE.Sprite(spriteMat)
    sprite.scale.set(40, 10, 1)
    sprite.position.set(0, 10, 0)

    const group = new THREE.Group()
    group.add(mesh)
    group.add(sprite)
    return group
  }, [selected, searchText])

  return (
    <div className="w-full h-full">
      <ForceGraph3D
        ref={fgRef}
        graphData={graphData}
        nodeThreeObject={nodeThreeObject}
        nodeThreeObjectExtend={false}
        nodeLabel={(n) => {
          const p = n.properties ?? {}
          const lines = [`<b>${n.labels?.join(', ')}</b>`, `<i>${n.label}</i>`]
          Object.entries(p).slice(0, 6).forEach(([k, v]) =>
            lines.push(`${k}: ${String(v).slice(0, 60)}`)
          )
          return lines.join('<br/>')
        }}
        linkLabel={(l) => `${l.type}${Object.keys(l.properties ?? {}).length ? ': ' + JSON.stringify(l.properties) : ''}`}
        linkColor={() => '#3a3a6a'}
        linkWidth={1.5}
        linkDirectionalArrowLength={6}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={0}
        onNodeClick={handleNodeClick}
        onLinkClick={handleLinkClick}
        backgroundColor="#0f0f1a"
        showNavInfo={false}
        nodeRelSize={1}
        enableNodeDrag={true}
        enableNavigationControls={true}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
      />
    </div>
  )
}
